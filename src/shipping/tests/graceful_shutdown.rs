use reqwest::Client;
use serde_json::json;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};
use tokio::signal::unix::{signal, SignalKind};
use tokio::io::{AsyncBufReadExt, BufReader};
use std::sync::Arc;
use tokio::sync::Mutex;

const SHIPPING_SERVICE_URL: &str = "http://localhost:8080";
const SHUTDOWN_TIMEOUT: u64 = 30;

// Helper to start shipping service and return child process + log reader
async fn start_service() -> (tokio::process::Child, Arc<Mutex<Vec<String>>>) {
    let mut child = tokio::process::Command::new("cargo")
        .arg("run")
        .current_dir("./src/shipping")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("Failed to start shipping service");

    let stdout = child.stdout.take().unwrap();
    let reader = BufReader::new(stdout).lines();
    let logs = Arc::new(Mutex::new(Vec::new()));
    let logs_clone = logs.clone();

    tokio::spawn(async move {
        let mut reader = reader;
        while let Ok(Some(line)) = reader.next_line().await {
            logs_clone.lock().await.push(line);
        }
    });

    // Wait for service to start
    tokio::time::sleep(Duration::from_secs(3)).await;

    (child, logs)
}

// Helper to send a long-running request that takes ~5 seconds to complete
async fn send_long_running_quote_request(client: &Client) -> reqwest::Response {
    // Send a quote request that takes time to process (simulate long-running)
    client.get(&format!("{SHIPPING_SERVICE_URL}/shipping/quote"))
        .query(&[("weight", "10"), ("destination", "US"), ("simulate_delay", "5")])
        .send()
        .await
        .expect("Failed to send quote request")
}

#[tokio::test]
async fn test_ac1_sigint_does_not_terminate_immediately() {
    // AC-1: When SIGINT is sent, service stays running for up to 30s for pending requests
    let (mut child, _logs) = start_service().await;
    let pid = child.id().unwrap() as i32;

    // Send SIGINT
    unsafe { libc::kill(pid, libc::SIGINT) };

    let start = Instant::now();
    let mut terminated_immediately = false;

    // Check if process is still running after 1 second
    tokio::time::sleep(Duration::from_secs(1)).await;
    match child.try_wait() {
        Ok(Some(_)) => terminated_immediately = true,
        Ok(None) => {},
        Err(_) => {},
    }

    assert!(!terminated_immediately, "Service terminated immediately after SIGINT (violates AC-1)");

    // Wait for up to SHUTDOWN_TIMEOUT + 2 seconds for process to exit
    let wait_result = tokio::time::timeout(Duration::from_secs(SHUTDOWN_TIMEOUT + 2), child.wait()).await;
    assert!(wait_result.is_ok(), "Service did not terminate within {SHUTDOWN_TIMEOUT} seconds after SIGINT (violates AC-1)");

    let elapsed = start.elapsed();
    assert!(elapsed >= Duration::from_secs(1) && elapsed <= Duration::from_secs(SHUTDOWN_TIMEOUT + 2), 
        "Service terminated too fast or too slow after SIGINT: {:?}", elapsed);
}

#[tokio::test]
async fn test_ac2_sigterm_does_not_terminate_immediately() {
    // AC-2: When SIGTERM is sent, service stays running for up to 30s for pending requests
    let (mut child, _logs) = start_service().await;
    let pid = child.id().unwrap() as i32;

    // Send SIGTERM
    unsafe { libc::kill(pid, libc::SIGTERM) };

    let start = Instant::now();
    let mut terminated_immediately = false;

    // Check if process is still running after 1 second
    tokio::time::sleep(Duration::from_secs(1)).await;
    match child.try_wait() {
        Ok(Some(_)) => terminated_immediately = true,
        Ok(None) => {},
        Err(_) => {},
    }

    assert!(!terminated_immediately, "Service terminated immediately after SIGTERM (violates AC-2)");

    // Wait for up to SHUTDOWN_TIMEOUT + 2 seconds for process to exit
    let wait_result = tokio::time::timeout(Duration::from_secs(SHUTDOWN_TIMEOUT + 2), child.wait()).await;
    assert!(wait_result.is_ok(), "Service did not terminate within {SHUTDOWN_TIMEOUT} seconds after SIGTERM (violates AC-2)");

    let elapsed = start.elapsed();
    assert!(elapsed >= Duration::from_secs(1) && elapsed <= Duration::from_secs(SHUTDOWN_TIMEOUT + 2), 
        "Service terminated too fast or too slow after SIGTERM: {:?}", elapsed);
}

#[tokio::test]
async fn test_ac3_in_flight_requests_complete_successfully_before_timeout() {
    // AC-3: In-flight requests return valid success if completed within 30s timeout
    let (mut child, _logs) = start_service().await;
    let pid = child.id().unwrap() as i32;
    let client = Client::builder()
        .timeout(Duration::from_secs(SHUTDOWN_TIMEOUT + 5))
        .build()
        .unwrap();

    // Send a request that takes ~5 seconds to complete
    let request_future = send_long_running_quote_request(&client);

    // Wait 1 second to ensure request is in flight, then send SIGINT
    tokio::time::sleep(Duration::from_secs(1)).await;
    unsafe { libc::kill(pid, libc::SIGINT) };

    // Wait for request response
    let response = request_future.await;
    assert_eq!(response.status().as_u16(), 200, "In-flight request failed after shutdown signal (violates AC-3)");

    let response_json = response.json::<serde_json::Value>().await;
    assert!(response_json.is_ok(), "In-flight request returned invalid JSON (violates AC-3)");
    assert!(response_json.unwrap().get("cost").is_some(), "Quote response missing cost field (violates AC-3)");

    // Wait for process to exit
    let _ = child.wait().await;
}

#[tokio::test]
async fn test_ac4_graceful_shutdown_start_log_emitted() {
    // AC-4: Warning log with "starting graceful shutdown" exists after signal receipt
    let (mut child, logs) = start_service().await;
    let pid = child.id().unwrap() as i32;

    // Send SIGINT
    unsafe { libc::kill(pid, libc::SIGINT) };

    // Wait 2 seconds for logs to be emitted
    tokio::time::sleep(Duration::from_secs(2)).await;

    let logs_lock = logs.lock().await;
    let has_shutdown_start_log = logs_lock.iter()
        .any(|line| line.contains("starting graceful shutdown") && line.contains("WARN"));

    assert!(has_shutdown_start_log, "Missing 'starting graceful shutdown' warning log after signal (violates AC-4)");

    // Wait for process to exit
    let _ = child.wait().await;
}

#[tokio::test]
async fn test_ac5_graceful_shutdown_complete_log_emitted() {
    // AC-5: Info log with "Graceful shutdown completed successfully" when all requests finish before timeout
    let (mut child, logs) = start_service().await;
    let pid = child.id().unwrap() as i32;
    let client = Client::builder()
        .timeout(Duration::from_secs(SHUTDOWN_TIMEOUT + 5))
        .build()
        .unwrap();

    // Send a short request, wait for it to complete, then send SIGINT
    let _ = client.get(&format!("{SHIPPING_SERVICE_URL}/shipping/quote"))
        .query(&[("weight", "10"), ("destination", "US")])
        .send()
        .await
        .unwrap();

    unsafe { libc::kill(pid, libc::SIGINT) };

    // Wait for process to exit
    let _ = tokio::time::timeout(Duration::from_secs(SHUTDOWN_TIMEOUT + 2), child.wait()).await;

    let logs_lock = logs.lock().await;
    let has_complete_log = logs_lock.iter()
        .any(|line| line.contains("Graceful shutdown completed successfully") && line.contains("INFO"));

    assert!(has_complete_log, "Missing 'Graceful shutdown completed successfully' info log (violates AC-5)");
}

#[tokio::test]
async fn test_ac6_shutdown_timeout_log_emitted_and_requests_terminated() {
    // AC-6: Warning log with timeout message if pending requests exceed 30s, requests are terminated
    let (mut child, logs) = start_service().await;
    let pid = child.id().unwrap() as i32;
    let client = Client::builder()
        .timeout(Duration::from_secs(SHUTDOWN_TIMEOUT + 10))
        .build()
        .unwrap();

    // Send a request that takes 40 seconds (longer than shutdown timeout)
    let request_future = client.get(&format!("{SHIPPING_SERVICE_URL}/shipping/quote"))
        .query(&[("weight", "10"), ("destination", "US"), ("simulate_delay", "40")])
        .send()
        .await;

    // Wait 1 second, then send SIGINT
    tokio::time::sleep(Duration::from_secs(1)).await;
    unsafe { libc::kill(pid, libc::SIGINT) };

    // Wait for process to exit (should exit after ~30s)
    let start = Instant::now();
    let _ = tokio::time::timeout(Duration::from_secs(SHUTDOWN_TIMEOUT + 5), child.wait()).await;
    let elapsed = start.elapsed();

    assert!(elapsed >= Duration::from_secs(SHUTDOWN_TIMEOUT) && elapsed <= Duration::from_secs(SHUTDOWN_TIMEOUT + 5),
        "Service did not exit after timeout period: {:?}", elapsed);

    // Check that request was terminated (should fail)
    assert!(request_future.is_err() || request_future.unwrap().status().is_server_error(),
        "Long-running request was not terminated after shutdown timeout (violates AC-6)");

    // Check timeout log exists
    let logs_lock = logs.lock().await;
    let has_timeout_log = logs_lock.iter()
        .any(|line| line.contains("Graceful shutdown timed out after 30 seconds") && line.contains("WARN"));

    assert!(has_timeout_log, "Missing shutdown timeout warning log (violates AC-6)");
}

#[tokio::test]
async fn test_ac7_existing_endpoints_functionality_unchanged() {
    // AC-7: Existing endpoints functionality remains identical to pre-implementation state
    let (_child, _logs) = start_service().await;
    let client = Client::builder()
        .timeout(Duration::from_secs(5))
        .build()
        .unwrap();

    // Test GET /shipping/quote endpoint
    let quote_response = client.get(&format!("{SHIPPING_SERVICE_URL}/shipping/quote"))
        .query(&[("weight", "10"), ("destination", "US")])
        .send()
        .await
        .expect("Failed to send quote request");

    assert_eq!(quote_response.status().as_u16(), 200, "Quote endpoint returned non-200 status (violates AC-7)");
    let quote_json = quote_response.json::<serde_json::Value>().await.unwrap();
    assert!(quote_json.get("cost").is_some(), "Quote response missing cost field (violates AC-7)");
    assert!(quote_json.get("currency").is_some(), "Quote response missing currency field (violates AC-7)");
    assert_eq!(quote_json.get("currency").unwrap().as_str().unwrap(), "USD", "Quote currency is not USD (violates AC-7)");

    // Test POST /shipping/order endpoint
    let order_response = client.post(&format!("{SHIPPING_SERVICE_URL}/shipping/order"))
        .json(&json!({
            "order_id": "test-123",
            "weight": 10,
            "destination": "US",
            "address": "123 Test St, Test City, 12345"
        }))
        .send()
        .await
        .expect("Failed to send order request");

    assert_eq!(order_response.status().as_u16(), 201, "Order endpoint returned non-201 status (violates AC-7)");
    let order_json = order_response.json::<serde_json::Value>().await.unwrap();
    assert!(order_json.get("tracking_id").is_some(), "Order response missing tracking_id field (violates AC-7)");
    assert!(order_json.get("estimated_delivery").is_some(), "Order response missing estimated_delivery field (violates AC-7)");
}
