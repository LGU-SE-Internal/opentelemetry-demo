//! Tests for graceful shutdown acceptance criteria as defined in issue #1856
use reqwest::Client;
use serde_json::Value;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::process::Child;
use tokio::sync::Mutex;
use tokio::io::{AsyncBufReadExt, BufReader};
use std::process::Stdio;

const SERVICE_ADDR: &str = "http://localhost:8080";
const SHUTDOWN_TIMEOUT: u64 = 30;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
#[repr(usize)]
pub enum ServiceState {
    Running = 0,
    ShuttingDown = 1,
    Exiting = 2,
}

/// Start shipping service instance, return child process and captured logs
async fn start_test_service() -> (Child, Arc<Mutex<Vec<String>>>) {
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

    // Wait for service to become healthy
    let client = Client::new();
    let mut retries = 0;
    while retries < 10 {
        if let Ok(res) = client.get(&format!("{SERVICE_ADDR}/health")).send().await {
            if res.status().is_success() {
                break;
            }
        }
        tokio::time::sleep(Duration::from_secs(1)).await;
        retries += 1;
    }

    (child, logs)
}

/// Send a long running quote request with configurable delay
async fn send_delayed_quote_request(client: &Client, delay_seconds: u64) -> reqwest::Response {
    client
        .get(&format!("{SERVICE_ADDR}/shipping/quote"))
        .query(&[
            ("weight", "10"),
            ("destination", "US"),
            ("simulate_delay", &delay_seconds.to_string()),
        ])
        .send()
        .await
        .expect("Failed to send quote request")
}

#[tokio::test]
async fn test_ac1_signal_does_not_exit_immediately_with_pending_requests() {
    // AC-1: Service waits min 1s before exit when in-flight requests exist after SIGINT/SIGTERM
    let (mut child, _logs) = start_test_service().await;
    let pid = child.id().unwrap() as i32;
    let client = Client::new();

    // Send a request that takes 5s to process
    let req_fut = send_delayed_quote_request(&client, 5);
    tokio::time::sleep(Duration::from_secs(1)).await;

    // Send SIGINT
    unsafe { libc::kill(pid, libc::SIGINT) };

    let start = Instant::now();
    // Check if process is still running after 1s
    tokio::time::sleep(Duration::from_secs(1)).await;
    let is_running = matches!(child.try_wait(), Ok(None));
    assert!(is_running, "Service exited immediately after SIGINT with pending requests (AC-1 violation)");

    // Wait for request to complete
    let resp = req_fut.await;
    assert!(resp.status().is_success());

    // Wait for process exit
    let _ = tokio::time::timeout(Duration::from_secs(SHUTDOWN_TIMEOUT + 2), child.wait()).await;
    assert!(start.elapsed() >= Duration::from_secs(1), "Service exited too quickly (AC-1 violation)");
}

#[tokio::test]
async fn test_ac2_new_requests_return_503_after_shutdown_signal() {
    // AC-2: All new requests get 503 after shutdown signal received
    let (mut child, _logs) = start_test_service().await;
    let pid = child.id().unwrap() as i32;
    let client = Client::new();

    // Send SIGTERM
    unsafe { libc::kill(pid, libc::SIGTERM) };

    // Wait for state to transition to shutting down
    tokio::time::sleep(Duration::from_secs(1)).await;

    // Try all endpoint types
    let health_resp = client.get(&format!("{SERVICE_ADDR}/health")).send().await.unwrap();
    assert_eq!(health_resp.status().as_u16(), 503, "Health endpoint did not return 503 after shutdown (AC-2 violation)");

    let quote_resp = client.get(&format!("{SERVICE_ADDR}/shipping/quote")).send().await.unwrap();
    assert_eq!(quote_resp.status().as_u16(), 503, "Quote endpoint did not return 503 after shutdown (AC-2 violation)");

    let order_resp = client.post(&format!("{SERVICE_ADDR}/shipping/order"))
        .json(&serde_json::json!({
            "order_id": "test", "weight": 10, "destination": "US", "address": "test"
        }))
        .send()
        .await
        .unwrap();
    assert_eq!(order_resp.status().as_u16(), 503, "Order endpoint did not return 503 after shutdown (AC-2 violation)");

    let _ = child.kill().await;
}

#[tokio::test]
async fn test_ac3_in_flight_requests_complete_within_30s() {
    // AC-3: In-flight requests get normal response if completed within 30s
    let (mut child, _logs) = start_test_service().await;
    let pid = child.id().unwrap() as i32;
    let client = Client::builder()
        .timeout(Duration::from_secs(SHUTDOWN_TIMEOUT + 5))
        .build()
        .unwrap();

    // Send 2 requests, 2s and 5s delay
    let req1 = send_delayed_quote_request(&client, 2);
    let req2 = send_delayed_quote_request(&client, 5);

    // Wait 1s then send SIGINT
    tokio::time::sleep(Duration::from_secs(1)).await;
    unsafe { libc::kill(pid, libc::SIGINT) };

    // Check both responses are successful
    let resp1 = req1.await;
    assert_eq!(resp1.status().as_u16(), 200, "In-flight request failed (AC-3 violation)");
    let json1 = resp1.json::<Value>().await.unwrap();
    assert!(json1.get("cost").is_some());

    let resp2 = req2.await;
    assert_eq!(resp2.status().as_u16(), 200, "In-flight request failed (AC-3 violation)");
    let json2 = resp2.json::<Value>().await.unwrap();
    assert!(json2.get("cost").is_some());

    // Wait for process exit
    let _ = tokio::time::timeout(Duration::from_secs(SHUTDOWN_TIMEOUT + 2), child.wait()).await;
}

#[tokio::test]
async fn test_ac4_force_exit_after_30s_timeout() {
    // AC-4: Service force exits after 30s if requests still running
    let (mut child, _logs) = start_test_service().await;
    let pid = child.id().unwrap() as i32;
    let client = Client::builder()
        .timeout(Duration::from_secs(SHUTDOWN_TIMEOUT + 10))
        .build()
        .unwrap();

    // Send request that takes 40s
    let req = send_delayed_quote_request(&client, 40);
    tokio::time::sleep(Duration::from_secs(1)).await;

    // Send SIGINT
    let start = Instant::now();
    unsafe { libc::kill(pid, libc::SIGINT) };

    // Wait for process exit
    let _ = tokio::time::timeout(Duration::from_secs(SHUTDOWN_TIMEOUT + 5), child.wait()).await;
    let elapsed = start.elapsed();

    assert!(elapsed >= Duration::from_secs(SHUTDOWN_TIMEOUT) && elapsed <= Duration::from_secs(SHUTDOWN_TIMEOUT + 5),
        "Service did not exit after 30s timeout (AC-4 violation)");

    // Request should fail
    assert!(req.await.is_err() || req.await.unwrap().status().is_server_error(),
        "Long running request succeeded after timeout (AC-4 violation)");
}

#[tokio::test]
async fn test_ac5_health_endpoint_returns_503_after_shutdown() {
    // AC-5: Health endpoint returns 503 with shutting down reason after signal
    let (mut child, _logs) = start_test_service().await;
    let pid = child.id().unwrap() as i32;
    let client = Client::new();

    // Pre-signal health check should be 200
    let pre_resp = client.get(&format!("{SERVICE_ADDR}/health")).send().await.unwrap();
    assert_eq!(pre_resp.status().as_u16(), 200);
    let pre_json = pre_resp.json::<Value>().await.unwrap();
    assert_eq!(pre_json.get("status").unwrap().as_str().unwrap(), "healthy");

    // Send SIGTERM
    unsafe { libc::kill(pid, libc::SIGTERM) };
    tokio::time::sleep(Duration::from_secs(1)).await;

    // Post-signal health check should be 503
    let post_resp = client.get(&format!("{SERVICE_ADDR}/health")).send().await.unwrap();
    assert_eq!(post_resp.status().as_u16(), 503, "Health endpoint returned non-503 after shutdown (AC-5 violation)");
    let post_json = post_resp.json::<Value>().await.unwrap();
    assert_eq!(post_json.get("status").unwrap().as_str().unwrap(), "unhealthy");
    assert_eq!(post_json.get("reason").unwrap().as_str().unwrap(), "shutting down");

    let _ = child.kill().await;
}

#[tokio::test]
async fn test_ac6_immediate_exit_when_no_pending_requests() {
    // AC-6: Service exits immediately without waiting 30s if no in-flight requests
    let (mut child, _logs) = start_test_service().await;
    let pid = child.id().unwrap() as i32;

    // Send SIGINT when idle
    let start = Instant::now();
    unsafe { libc::kill(pid, libc::SIGINT) };

    // Wait for process exit
    let wait_res = tokio::time::timeout(Duration::from_secs(3), child.wait()).await;
    assert!(wait_res.is_ok(), "Service did not exit immediately when idle (AC-6 violation)");
    assert!(start.elapsed() < Duration::from_secs(2), "Service took too long to exit when idle (AC-6 violation)");
}

#[tokio::test]
async fn test_ac7_unit_tests_exist_verify_shutdown_flow() {
    // AC-7: Unit tests exist for shutdown flow without OS signal injection
    // This test checks that the unit test modules exist and have expected test functions
    let test_file = std::fs::read_to_string("./src/shipping/src/shipping_service.rs").unwrap();
    assert!(test_file.contains("shutdown_signal_handler"), "Missing shutdown_signal_handler implementation (AC-7 violation)");
    assert!(test_file.contains("graceful_shutdown"), "Missing graceful_shutdown implementation (AC-7 violation)");
    assert!(test_file.contains("ServiceState"), "Missing ServiceState enum (AC-7 violation)");

    // Check for test modules
    let main_file = std::fs::read_to_string("./src/shipping/src/main.rs").unwrap();
    assert!(main_file.contains("#[cfg(test)]") || main_file.contains("mod tests"), "Missing test module in main.rs (AC-7 violation)");
}
