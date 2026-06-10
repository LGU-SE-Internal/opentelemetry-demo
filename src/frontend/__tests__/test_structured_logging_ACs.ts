import * as fs from 'fs';
import * as path from 'path';
import { spawn } from 'child_process';
import { test, expect } from '@jest/globals';

const SERVER_PATH = path.join(__dirname, '../server.ts');
const serverContent = fs.readFileSync(SERVER_PATH, 'utf8');

describe('Structured Logging Acceptance Criteria Tests', () => {
  // AC-1: All console.log/console.error calls in server.ts are replaced with logger.info/logger.error
  test('test_ac1_no_console_statements_in_server_ts', () => {
    // Check no console.log remaining
    const consoleLogMatches = serverContent.match(/console\.log\(/g);
    expect(consoleLogMatches).toBeNull();
    
    // Check no console.error remaining
    const consoleErrorMatches = serverContent.match(/console\.error\(/g);
    expect(consoleErrorMatches).toBeNull();
    
    // Check logger calls exist
    const loggerInfoMatches = serverContent.match(/logger\.info\(/g);
    expect(loggerInfoMatches).not.toBeNull();
    expect(loggerInfoMatches?.length).toBeGreaterThan(0);
    
    const loggerErrorMatches = serverContent.match(/logger\.error\(/g);
    expect(loggerErrorMatches).not.toBeNull();
    expect(loggerErrorMatches?.length).toBeGreaterThan(0);
  });

  // AC-2: All log entries have mandatory fields: timestamp, level, service.name, message
  test('test_ac2_log_entries_have_mandatory_fields', async () => {
    // Start server and capture log output
    const serverProcess = spawn('node', [SERVER_PATH], {
      env: { ...process.env, NODE_ENV: 'production', PORT: '3001' },
    });

    let logOutput = '';
    serverProcess.stdout.on('data', (data) => {
      logOutput += data.toString();
    });

    // Wait for server to start
    await new Promise(resolve => setTimeout(resolve, 3000));
    
    // Kill server process
    serverProcess.kill();

    // Parse all log lines
    const logLines = logOutput.trim().split('\n');
    expect(logLines.length).toBeGreaterThan(0);

    for (const line of logLines) {
      if (!line.trim()) continue;
      
      // Log should be valid JSON
      let logEntry;
      try {
        logEntry = JSON.parse(line);
      } catch (e) {
        expect(e).toBeNull(); // Fail if not JSON
        return;
      }

      // Check mandatory fields exist and are correct
      expect(logEntry.timestamp).toBeDefined();
      expect(typeof logEntry.timestamp).toBe('number');
      expect(logEntry.timestamp).toBeGreaterThan(Date.now() - 10000); // Last 10 seconds
      
      expect(logEntry.level).toBeDefined();
      expect(['info', 'error']).toContain(logEntry.level);
      
      expect(logEntry['service.name']).toBeDefined();
      expect(logEntry['service.name']).toBe('frontend');
      
      expect(logEntry.message).toBeDefined();
      expect(typeof logEntry.message).toBe('string');
      expect(logEntry.message.length).toBeGreaterThan(0);
    }
  });

  // AC-3: All original log information is preserved
  test('test_ac3_all_original_log_info_preserved', () => {
    // Check original log messages exist as message or attributes
    const expectedLogContents = [
      'Frontend server listening on port',
      'Running in',
      'Configuration loaded',
      'Error initializing server',
      'Request failed',
      'Shutting down server'
    ];

    for (const content of expectedLogContents) {
      const existsInLoggerCalls = serverContent.includes(`logger.info("${content}`) || 
                                serverContent.includes(`logger.error("${content}`) ||
                                serverContent.includes(`logger.info('${content}`) ||
                                serverContent.includes(`logger.error('${content}`);
      expect(existsInLoggerCalls).toBeTruthy();
    }
  });

  // AC-4: Log entries in trace context have trace.id and span.id
  test('test_ac4_logs_have_trace_and_span_ids_in_trace_context', async () => {
    // Mock trace context and check logs include trace.id and span.id
    const testCode = `
      import { trace } from '@opentelemetry/api';
      import { logger } from './utils/telemetry';
      
      // Create a test span
      const tracer = trace.getTracer('test');
      tracer.startActiveSpan('test-span', (span) => {
        logger.info('Test log in trace context');
        span.end();
      });
    `;

    const testFile = path.join(__dirname, 'temp-trace-test.ts');
    fs.writeFileSync(testFile, testCode);

    const testProcess = spawn('npx', ['ts-node', testFile], {
      env: { ...process.env, NODE_ENV: 'production' },
    });

    let logOutput = '';
    testProcess.stdout.on('data', (data) => {
      logOutput += data.toString();
    });

    await new Promise(resolve => testProcess.on('exit', resolve));

    // Clean up test file
    fs.unlinkSync(testFile);

    const logLines = logOutput.trim().split('\n');
    expect(logLines.length).toBeGreaterThan(0);

    let foundTraceLog = false;
    for (const line of logLines) {
      if (!line.includes('Test log in trace context')) continue;
      
      foundTraceLog = true;
      const logEntry = JSON.parse(line);
      
      expect(logEntry['trace.id']).toBeDefined();
      expect(typeof logEntry['trace.id']).toBe('string');
      expect(logEntry['trace.id'].length).toBe(32); // OT trace ID length
      
      expect(logEntry['span.id']).toBeDefined();
      expect(typeof logEntry['span.id']).toBe('string');
      expect(logEntry['span.id'].length).toBe(16); // OT span ID length
    }

    expect(foundTraceLog).toBeTruthy();
  });

  // AC-5: Log output is valid JSON in development and production environments
  test.each(['development', 'production'])('test_ac5_logs_are_valid_json_in_%s_env', async (env) => {
    const serverProcess = spawn('node', [SERVER_PATH], {
      env: { ...process.env, NODE_ENV: env, PORT: env === 'development' ? '3002' : '3003' },
    });

    let logOutput = '';
    serverProcess.stdout.on('data', (data) => {
      logOutput += data.toString();
    });

    serverProcess.stderr.on('data', (data) => {
      logOutput += data.toString();
    });

    // Wait for server to start
    await new Promise(resolve => setTimeout(resolve, 3000));
    serverProcess.kill();

    const logLines = logOutput.trim().split('\n');
    expect(logLines.length).toBeGreaterThan(0);

    for (const line of logLines) {
      if (!line.trim()) continue;
      
      // Validate JSON
      expect(() => JSON.parse(line)).not.toThrow();
    }
  });
});
