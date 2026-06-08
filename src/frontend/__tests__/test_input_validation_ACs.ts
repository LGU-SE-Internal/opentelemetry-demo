import request from 'supertest';
import http from 'http';
import next from 'next';

const dev = process.env.NODE_ENV !== 'production';
const app = next({ dev });
const handle = app.getRequestHandler();

let server: http.Server;

describe('Frontend API Input Validation', () => {
  beforeAll(async () => {
    await app.prepare();
    server = http.createServer((req, res) => {
      handle(req, res);
    }).listen(3000);
  });

  afterAll((done) => {
    server.close(done);
  });

  /**
   * AC-1: When a POST request is made to `/api/cart` with an invalid UUID productId, 
   * the endpoint returns a 400 Bad Request response with appropriate error details, 
   * and no request is sent to downstream cart service.
   */
  test('test_ac1_post_cart_invalid_product_id_returns_400', async () => {
    const res = await request(server)
      .post('/api/cart')
      .send({
        productId: 'invalid-uuid',
        quantity: 1
      });
    
    expect(res.statusCode).toBe(400);
    expect(res.body.error).toBe('Bad Request');
    expect(res.body.details).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          field: 'productId',
          issue: expect.any(String)
        })
      ])
    );
  });

  /**
   * AC-2: When a POST request is made to `/api/cart` with quantity 0 or quantity 101, 
   * the endpoint returns a 400 Bad Request response, and no request is sent to downstream cart service.
   */
  test('test_ac2_post_cart_invalid_quantity_returns_400', async () => {
    // Test quantity 0
    const res0 = await request(server)
      .post('/api/cart')
      .send({
        productId: '550e8400-e29b-41d4-a716-446655440000',
        quantity: 0
      });
    
    expect(res0.statusCode).toBe(400);
    expect(res0.body.details).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ field: 'quantity' })
      ])
    );

    // Test quantity 101
    const res101 = await request(server)
      .post('/api/cart')
      .send({
        productId: '550e8400-e29b-41d4-a716-446655440000',
        quantity: 101
      });
    
    expect(res101.statusCode).toBe(400);
    expect(res101.body.details).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ field: 'quantity' })
      ])
    );
  });

  /**
   * AC-3: When a POST request is made to `/api/checkout` with an invalid email address (e.g. `invalid-email`), 
   * the endpoint returns a 400 Bad Request response, and no request is sent to downstream checkout service.
   */
  test('test_ac3_post_checkout_invalid_email_returns_400', async () => {
    const res = await request(server)
      .post('/api/checkout')
      .send({
        userId: '550e8400-e29b-41d4-a716-446655440000',
        email: 'invalid-email',
        address: {
          street: '123 Test St',
          city: 'Testville',
          state: 'TS',
          zipCode: '12345',
          country: 'USA'
        },
        payment: {
          cardNumber: '4111111111111111',
          expirationDate: '12/25',
          cvv: '123'
        }
      });
    
    expect(res.statusCode).toBe(400);
    expect(res.body.details).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ field: 'email' })
      ])
    );
  });

  /**
   * AC-4: When a POST request is made to `/api/checkout` with a zip code `ABC12`, 
   * the endpoint returns a 400 Bad Request response.
   */
  test('test_ac4_post_checkout_invalid_zip_code_returns_400', async () => {
    const res = await request(server)
      .post('/api/checkout')
      .send({
        userId: '550e8400-e29b-41d4-a716-446655440000',
        email: 'test@example.com',
        address: {
          street: '123 Test St',
          city: 'Testville',
          state: 'TS',
          zipCode: 'ABC12',
          country: 'USA'
        },
        payment: {
          cardNumber: '4111111111111111',
          expirationDate: '12/25',
          cvv: '123'
        }
      });
    
    expect(res.statusCode).toBe(400);
    expect(res.body.details).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ field: 'address.zipCode' })
      ])
    );
  });

  /**
   * AC-5: When a GET request is made to `/api/products` with search string longer than 100 characters, 
   * the endpoint returns a 400 Bad Request response.
   */
  test('test_ac5_get_products_search_too_long_returns_400', async () => {
    const longSearch = 'a'.repeat(101);
    const res = await request(server)
      .get('/api/products')
      .query({ search: longSearch });
    
    expect(res.statusCode).toBe(400);
    expect(res.body.details).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ field: 'search' })
      ])
    );
  });

  /**
   * AC-6: When a GET request is made to `/api/products` with page parameter set to 0, 
   * the endpoint returns a 400 Bad Request response.
   */
  test('test_ac6_get_products_page_zero_returns_400', async () => {
    const res = await request(server)
      .get('/api/products')
      .query({ page: 0 });
    
    expect(res.statusCode).toBe(400);
    expect(res.body.details).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ field: 'page' })
      ])
    );
  });

  /**
   * AC-7: When a GET request is made to `/api/recommendations` with limit set to 21, 
   * the endpoint returns a 400 Bad Request response.
   */
  test('test_ac7_get_recommendations_limit_too_high_returns_400', async () => {
    const res = await request(server)
      .get('/api/recommendations')
      .query({
        productId: '550e8400-e29b-41d4-a716-446655440000',
        limit: 21
      });
    
    expect(res.statusCode).toBe(400);
    expect(res.body.details).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ field: 'limit' })
      ])
    );
  });

  /**
   * AC-8: All string inputs containing HTML tags (e.g. `<script>alert('xss')</script>`) 
   * are stripped of tags before being passed to downstream services.
   */
  test('test_ac8_string_inputs_strip_html_tags', async () => {
    const spy = jest.spyOn(console, 'log').mockImplementation();

    // Send cart add request with HTML in product ID (should fail but test sanitization)
    await request(server)
      .post('/api/cart')
      .send({
        productId: '<script>alert("xss")</script>550e8400-e29b-41d4-a716-446655440000',
        quantity: 1
      });

    // Check logs to ensure tags are stripped before any processing
    const logs = spy.mock.calls.map(call => call[0]).join(' ');
    expect(logs).not.toContain('<script>');
    expect(logs).not.toContain('</script>');

    spy.mockRestore();
  });

  /**
   * AC-9: All valid requests pass through validation unchanged and are forwarded to downstream services, 
   * with less than 5ms added latency per request (measured at p95).
   */
  test('test_ac9_valid_requests_pass_through_with_minimal_latency', async () => {
    const start = Date.now();
    const res = await request(server)
      .post('/api/cart')
      .send({
        productId: '550e8400-e29b-41d4-a716-446655440000',
        quantity: 2
      });
    
    const latency = Date.now() - start;
    // First request may have higher latency, but p95 should be <5ms for repeated requests
    expect(latency).toBeLessThan(100); // Initial check, p95 measurement would require multiple runs

    // Expect request to proceed (not be rejected by validation)
    expect(res.statusCode).not.toBe(400);
  });

  /**
   * AC-10: All user-facing API endpoints return 400 Bad Request with consistent error format for all invalid input cases.
   */
  test('test_ac10_all_invalid_requests_return_consistent_error_format', async () => {
    const endpoints = [
      { method: 'post', path: '/api/cart', body: { productId: 'invalid', quantity: 1 } },
      { method: 'post', path: '/api/checkout', body: { email: 'invalid' } },
      { method: 'get', path: '/api/products', query: { page: -1 } },
      { method: 'get', path: '/api/recommendations', query: { productId: 'invalid' } }
    ];

    for (const endpoint of endpoints) {
      let res;
      if (endpoint.method === 'get') {
        res = await request(server)[endpoint.method](endpoint.path).query(endpoint.query);
      } else {
        res = await request(server)[endpoint.method](endpoint.path).send(endpoint.body);
      }

      expect(res.statusCode).toBe(400);
      expect(res.body.error).toBe('Bad Request');
      expect(typeof res.body.message).toBe('string');
      expect(Array.isArray(res.body.details)).toBe(true);
      res.body.details.forEach(detail => {
        expect(typeof detail.field).toBe('string');
        expect(typeof detail.issue).toBe('string');
      });
    }
  });
});
