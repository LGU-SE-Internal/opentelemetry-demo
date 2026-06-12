const grpc = require('@grpc/grpc-js');
const protoLoader = require('@grpc/proto-loader');
const assert = require('assert');
const path = require('path');

// Load standard gRPC health check proto
const PROTO_PATH = path.join(__dirname, '../../proto/grpc/health/v1/health.proto');
const packageDefinition = protoLoader.loadSync(PROTO_PATH, {
  keepCase: true,
  longs: String,
  enums: String,
  defaults: true,
  oneofs: true
});
const healthProto = grpc.loadPackageDefinition(packageDefinition).grpc.health.v1;

const SERVICE_ADDRESS = process.env.CURRENCY_SERVICE_ADDR || 'localhost:7000';
const client = new healthProto.Health(SERVICE_ADDRESS, grpc.credentials.createInsecure());

describe('gRPC Health Check Endpoints', () => {
  beforeAll(() => {
    // Wait for service to be available before running tests
    return new Promise((resolve, reject) => {
      const deadline = Date.now() + 30000; // 30s timeout for service to start
      client.waitForReady(deadline, (err) => {
        if (err) reject(err);
        else resolve();
      });
    });
  });

  afterAll(() => {
    client.close();
  });

  // AC-1: Check with empty service name returns SERVING when service is running
  test('test_ac1_empty_service_liveness_returns_serving', (done) => {
    client.Check({ service: '' }, (err, response) => {
      assert.ifError(err);
      assert.strictEqual(response.status, 'SERVING');
      done();
    });
  });

  // AC-2: Check with "currencyservice" returns NOT_SERVING during initialization
  test('test_ac2_currencyservice_readiness_returns_not_serving_during_startup', (done) => {
    // Test scenario: service is starting up, data not loaded yet
    // We simulate this by checking immediately after process start (before init completes)
    // For this test, we assume we can connect to an uninitialized instance
    client.Check({ service: 'currencyservice' }, (err, response) => {
      // This test expects NOT_SERVING when service is not initialized
      // When no implementation exists, this will fail as expected
      assert.ifError(err);
      assert.strictEqual(response.status, 'NOT_SERVING');
      done();
    });
  });

  // AC-3: Check with "currencyservice" returns SERVING when fully initialized
  test('test_ac3_currencyservice_readiness_returns_serving_when_healthy', (done) => {
    // Wait for service to finish initialization before running this test
    setTimeout(() => {
      client.Check({ service: 'currencyservice' }, (err, response) => {
        assert.ifError(err);
        assert.strictEqual(response.status, 'SERVING');
        done();
      });
    }, 10000); // Wait 10s for service to initialize
  });

  // AC-4: Service status becomes NOT_SERVING within 5s when critical dependency fails
  test('test_ac4_currencyservice_status_updates_on_dependency_failure', (done) => {
    // First confirm service is healthy
    client.Check({ service: 'currencyservice' }, (err, response) => {
      assert.ifError(err);
      assert.strictEqual(response.status, 'SERVING');

      // Simulate downstream dependency failure (e.g. disconnect data store)
      // This step would be handled by test infrastructure to break dependency
      console.log('Simulating critical dependency failure...');

      // Check status every 500ms for up to 5 seconds
      const checkInterval = setInterval(() => {
        client.Check({ service: 'currencyservice' }, (err, response) => {
          if (response?.status === 'NOT_SERVING') {
            clearInterval(checkInterval);
            done();
          }
        });
      }, 500);

      // Fail test if status does not update within 5 seconds
      setTimeout(() => {
        clearInterval(checkInterval);
        done(new Error('Service status did not update to NOT_SERVING within 5 seconds after dependency failure'));
      }, 5500);
    });
  });

  // AC-5: Watch endpoint streams status updates on state changes
  test('test_ac5_watch_endpoint_streams_status_updates', (done) => {
    const statusUpdates = [];
    const call = client.Watch({ service: 'currencyservice' });

    call.on('data', (response) => {
      statusUpdates.push(response.status);
      // Expect first update to be current status, then another update when state changes
      if (statusUpdates.length >= 2) {
        call.cancel();
        assert.notStrictEqual(statusUpdates[0], statusUpdates[1]);
        done();
      }
    });

    call.on('error', (err) => {
      if (err.code !== grpc.status.CANCELLED) {
        done(err);
      }
    });

    // Simulate state change after 1 second to trigger update
    setTimeout(() => {
      console.log('Simulating service state change...');
    }, 1000);

    // Fail test if no updates received within 10 seconds
    setTimeout(() => {
      call.cancel();
      done(new Error('No status updates received on watch stream within 10 seconds'));
    }, 10000);
  });

  // AC-6: Health check requests generate OpenTelemetry spans with correct attributes
  test('test_ac6_health_checks_have_correct_otel_spans', async () => {
    // Make a Check request
    await new Promise((resolve, reject) => {
      client.Check({ service: 'currencyservice' }, (err, response) => {
        if (err) reject(err);
        else resolve(response);
      });
    });

    // Make a Watch request
    const call = client.Watch({ service: 'currencyservice' });
    call.on('data', () => call.cancel());

    // Check exported spans (assumes OTel collector test endpoint is available)
    // This will verify:
    // - rpc.service = "grpc.health.v1.Health"
    // - rpc.method = "Check" / "Watch"
    // - rpc.grpc.status_code = OK (0)
    // Implementation of span retrieval would depend on test infrastructure
    // For now, this test will fail as no instrumentation exists
    const spans = await getExportedSpans();
    const checkSpan = spans.find(s => s.attributes['rpc.method'] === 'Check');
    const watchSpan = spans.find(s => s.attributes['rpc.method'] === 'Watch');

    assert.ok(checkSpan, 'Check method span not found');
    assert.strictEqual(checkSpan.attributes['rpc.service'], 'grpc.health.v1.Health');
    assert.strictEqual(checkSpan.attributes['rpc.grpc.status_code'], 0);

    assert.ok(watchSpan, 'Watch method span not found');
    assert.strictEqual(watchSpan.attributes['rpc.service'], 'grpc.health.v1.Health');
    assert.strictEqual(watchSpan.attributes['rpc.grpc.status_code'], 1); // CANCELLED
  });

  // AC-7: Health check latency <100ms p99 under load
  test('test_ac7_health_check_latency_p99_under_100ms', async () => {
    const NUM_REQUESTS = 1000;
    const latencies = [];

    for (let i = 0; i < NUM_REQUESTS; i++) {
      const start = Date.now();
      await new Promise((resolve, reject) => {
        client.Check({ service: '' }, (err, response) => {
          if (err) reject(err);
          else {
            latencies.push(Date.now() - start);
            resolve();
          }
        });
      });
    }

    // Calculate 99th percentile
    latencies.sort((a, b) => a - b);
    const p99 = latencies[Math.ceil(0.99 * NUM_REQUESTS) - 1];
    assert.ok(p99 < 100, `99th percentile latency ${p99}ms exceeds 100ms threshold`);
  });
});

// Helper function to retrieve exported OTel spans from test collector
async function getExportedSpans() {
  // Implementation would fetch spans from OTel test collector endpoint
  // For now return empty array to make test fail as expected
  return [];
}
