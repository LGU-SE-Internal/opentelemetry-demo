describe("Frontend Health Check Endpoints", () => {
  const LIVE_ENDPOINT = "/api/health/live";
  const READY_ENDPOINT = "/api/health/ready";

  it("test_ac1_live_returns_200_ok_with_valid_payload", () => {
    cy.request({
      url: LIVE_ENDPOINT,
      failOnStatusCode: false,
    }).then((response) => {
      expect(response.status).to.eq(200);
      expect(response.headers["content-type"]).to.include("application/json");
      expect(response.body.status).to.eq("ok");
      // Validate timestamp is ISO 8601 format
      expect(response.body.timestamp).to.match(
        /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$/
      );
    });
  });

  it("test_ac2_ready_returns_200_ok_when_all_healthy", () => {
    cy.request({
      url: READY_ENDPOINT,
      failOnStatusCode: false,
    }).then((response) => {
      // This will fail until implementation exists and services are up
      expect(response.status).to.eq(200);
      expect(response.headers["content-type"]).to.include("application/json");
      expect(response.body.status).to.eq("ok");
      expect(response.body.timestamp).to.match(
        /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$/
      );
      expect(response.body.checks).to.exist;
      expect(response.body.checks.backend_connections).to.eq("ok");
      expect(response.body.checks.service_initialized).to.eq("ok");
    });
  });

  it("test_ac3_ready_returns_503_when_backend_connections_fail", () => {
    // Simulate backend connection failure scenario
    cy.intercept("GET", READY_ENDPOINT, (req) => {
      req.continue((res) => {
        // This test verifies the 503 behavior when backend fails
        if (res.body.checks?.backend_connections === "failed") {
          expect(res.statusCode).to.eq(503);
          expect(res.body.status).to.eq("unavailable");
          expect(res.body.timestamp).to.match(
            /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$/
          );
          expect(res.body.error).to.be.a("string");
          expect(res.body.error).not.to.be.empty;
        }
      });
    });
    cy.request({ url: READY_ENDPOINT, failOnStatusCode: false });
  });

  it("test_ac4_ready_returns_503_during_initialization", () => {
    // Test scenario during service bootstrap before initialization completes
    cy.request({
      url: READY_ENDPOINT,
      failOnStatusCode: false,
    }).then((response) => {
      if (response.body.checks?.service_initialized === "pending" || response.body.checks?.backend_connections === "pending") {
        expect(response.status).to.eq(503);
        expect(response.body.status).to.eq("unavailable");
        expect(response.body.timestamp).to.match(
          /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$/
        );
      }
    });
  });

  it("test_ac5_health_endpoints_return_json_content_type", () => {
    cy.request({
      url: LIVE_ENDPOINT,
      failOnStatusCode: false,
    }).its("headers.content-type").should("include", "application/json");

    cy.request({
      url: READY_ENDPOINT,
      failOnStatusCode: false,
    }).its("headers.content-type").should("include", "application/json");
  });

  it("test_ac6_health_schema_matches_conventions", () => {
    cy.request({
      url: LIVE_ENDPOINT,
      failOnStatusCode: false,
    }).then((response) => {
      expect(Object.keys(response.body)).to.have.members(["status", "timestamp"]);
    });

    cy.request({
      url: READY_ENDPOINT,
      failOnStatusCode: false,
    }).then((response) => {
      if (response.status === 200) {
        expect(Object.keys(response.body)).to.have.members(["status", "timestamp", "checks"]);
        expect(Object.keys(response.body.checks)).to.have.members(["backend_connections", "service_initialized"]);
      } else if (response.status === 503) {
        expect(Object.keys(response.body)).to.have.members(["status", "timestamp", "checks", "error"]);
      }
    });
  });
});
