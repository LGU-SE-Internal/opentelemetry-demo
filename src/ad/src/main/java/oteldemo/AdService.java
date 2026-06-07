/*
 * Copyright The OpenTelemetry Authors
 * SPDX-License-Identifier: Apache-2.0
 */

package oteldemo;

import com.google.common.collect.ImmutableListMultimap;
import com.google.common.collect.Iterables;
import io.grpc.*;
import io.grpc.health.v1.HealthCheckResponse.ServingStatus;
import io.grpc.netty.shaded.io.grpc.netty.NettyServerBuilder;
import io.grpc.protobuf.services.*;
import io.grpc.stub.StreamObserver;
import io.grpc.netty.shaded.io.netty.buffer.Unpooled;
import io.grpc.netty.shaded.io.netty.channel.ChannelHandlerContext;
import io.grpc.netty.shaded.io.netty.channel.ChannelInboundHandlerAdapter;
import io.grpc.netty.shaded.io.netty.channel.ChannelInitializer;
import io.grpc.netty.shaded.io.netty.channel.socket.SocketChannel;
import io.grpc.netty.shaded.io.netty.handler.codec.http.DefaultFullHttpResponse;
import io.grpc.netty.shaded.io.netty.handler.codec.http.FullHttpResponse;
import io.grpc.netty.shaded.io.netty.handler.codec.http.HttpMethod;
import io.grpc.netty.shaded.io.netty.handler.codec.http.HttpObjectAggregator;
import io.grpc.netty.shaded.io.netty.handler.codec.http.HttpRequest;
import io.grpc.netty.shaded.io.netty.handler.codec.http.HttpResponseStatus;
import io.grpc.netty.shaded.io.netty.handler.codec.http.HttpServerCodec;
import io.grpc.netty.shaded.io.netty.handler.codec.http.HttpUtil;
import io.grpc.netty.shaded.io.netty.util.CharsetUtil;
import io.opentelemetry.api.GlobalOpenTelemetry;
import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.baggage.Baggage;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.metrics.LongCounter;
import io.opentelemetry.api.metrics.Meter;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.StatusCode;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.context.Context;
import io.opentelemetry.context.Scope;
import io.opentelemetry.instrumentation.annotations.SpanAttribute;
import io.opentelemetry.instrumentation.annotations.WithSpan;
import io.prometheus.metrics.core.metrics.Counter;
import io.prometheus.metrics.exporter.httpserver.HTTPServer;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Random;
import org.apache.logging.log4j.Level;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import oteldemo.Demo.Ad;
import oteldemo.Demo.AdRequest;
import oteldemo.Demo.AdResponse;
import oteldemo.problempattern.GarbageCollectionTrigger;
import oteldemo.problempattern.CPULoad;
import dev.openfeature.contrib.providers.flagd.FlagdOptions;
import dev.openfeature.contrib.providers.flagd.FlagdProvider;
import dev.openfeature.sdk.Client;
import dev.openfeature.sdk.EvaluationContext;
import dev.openfeature.sdk.MutableContext;
import dev.openfeature.sdk.OpenFeatureAPI;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;
import io.grpc.netty.shaded.io.grpc.netty.GrpcSslContexts;
import io.grpc.netty.shaded.io.netty.handler.ssl.SslContext;
import io.grpc.netty.shaded.io.netty.handler.ssl.SslContextBuilder;
import io.grpc.netty.shaded.io.netty.handler.ssl.ClientAuth;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.security.cert.CertificateException;


public final class AdService {

  private static final Logger logger = LogManager.getLogger(AdService.class);

  @SuppressWarnings("FieldCanBeLocal")
  private static final int MAX_ADS_TO_SERVE = 2;

  private Server server;
  private HealthStatusManager healthMgr;
  private HTTPServer prometheusServer;
  private volatile boolean isReady = false;
  private final AtomicInteger inFlightRequests = new AtomicInteger(0);

  private static class HealthHttpHandler extends ChannelInboundHandlerAdapter {
    private final AdService service;

    public HealthHttpHandler(AdService service) {
      this.service = service;
    }

    @Override
    public void channelRead(ChannelHandlerContext ctx, Object msg) throws Exception {
      if (msg instanceof HttpRequest req) {
        if (req.method() != HttpMethod.GET) {
          sendResponse(ctx, HttpResponseStatus.METHOD_NOT_ALLOWED, "METHOD_NOT_ALLOWED");
          return;
        }

        String path = req.uri();
        if (path.equals("/liveness")) {
          // Liveness: return UP if JVM is running
          sendResponse(ctx, HttpResponseStatus.OK, "OK");
        } else if (path.equals("/readiness")) {
          // Readiness: return READY only if service is fully initialized
          if (service.isReady) {
            sendResponse(ctx, HttpResponseStatus.OK, "READY");
          } else {
            sendResponse(ctx, HttpResponseStatus.SERVICE_UNAVAILABLE, "NOT_READY");
          }
        } else {
          // Pass through to gRPC handler
          ctx.fireChannelRead(msg);
        }
      } else {
        ctx.fireChannelRead(msg);
      }
    }

    private void sendResponse(ChannelHandlerContext ctx, HttpResponseStatus status, String content) {
      FullHttpResponse response = new DefaultFullHttpResponse(
        io.grpc.netty.shaded.io.netty.handler.codec.http.HttpVersion.HTTP_1_1,
        status,
        Unpooled.copiedBuffer(content, CharsetUtil.UTF_8)
      );
      HttpUtil.setContentLength(response, response.content().readableBytes());
      response.headers().set("Content-Type", "text/plain; charset=UTF-8");
      ctx.writeAndFlush(response);
    }

    @Override
    public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) throws Exception {
      ctx.close();
    }
  }

  // DEMO: this counter and its `/metrics` HTTP exporter use the Prometheus
  // Java client library rather than the OpenTelemetry SDK. It is here to
  // illustrate how non-OTel custom metrics (e.g. existing Prometheus
  // instrumentation that an organization already owns) can be ingested into
  // an OpenTelemetry pipeline via the Collector's `prometheus` receiver --
  // a common bridging pattern during OTel adoption.
  //
  // Recommendation: this is a *transitional* pattern. New custom metrics
  // should be created directly with the OpenTelemetry SDK (see the
  // `adRequestsCounter` below), and existing Prometheus-client metrics
  // should be migrated over time. See `src/ad/README.md` for details.
  private static final Counter adsServedCounter =
      Counter.builder()
          .name("demo_ad_served_total")
          .help("Total number of ads served, labeled by category")
          .labelNames("category")
          .register();

  private static final AdService service = new AdService();
  private static final Tracer tracer = GlobalOpenTelemetry.getTracer("ad");
  private static final Meter meter = GlobalOpenTelemetry.getMeter("ad");

  private static final LongCounter adRequestsCounter =
      meter
          .counterBuilder("demo.ad.requests")
          .setDescription("Counts ad requests by request and response type")
          .build();

  private static final AttributeKey<String> adRequestTypeKey =
      AttributeKey.stringKey("demo.ad.request_type");
  private static final AttributeKey<String> adResponseTypeKey =
      AttributeKey.stringKey("demo.ad.response_type");

  private void validateTlsFile(String path) {
    File file = new File(path);
    if (!file.exists()) {
      throw new RuntimeException("Failed to read TLS file at " + path + ": File not found");
    }
    if (!file.isFile()) {
      throw new RuntimeException("Failed to read TLS file at " + path + ": Not a file");
    }
    if (!file.canRead()) {
      throw new RuntimeException("Failed to read TLS file at " + path + ": Permission denied");
    }
  }

  public static Server startServer(int port) throws IOException {
    AdService service = getInstance();
    // Override port for testing
    service.startWithPort(port);
    return service.server;
  }

  private void startWithPort(int port) throws IOException {
    internalStart(port, null);
  }

  private void start() throws IOException {
    int port =
        Integer.parseInt(
            Optional.ofNullable(System.getenv("AD_PORT"))
                .orElseThrow(
                    () ->
                        new IllegalStateException(
                            "environment vars: AD_PORT must not be null")));
    Integer prometheusPort =
        Integer.parseInt(Optional.ofNullable(System.getenv("AD_PROMETHEUS_PORT")).orElse("9465"));
    internalStart(port, prometheusPort);
  }

  private void internalStart(int port, Integer prometheusPort) throws IOException {
    if (prometheusPort != null) {
      prometheusServer = HTTPServer.builder().port(prometheusPort).buildAndStart();
      logger.info(
          "Prometheus metrics endpoint started, listening on " + prometheusServer.getPort() + "/metrics");
    }
    healthMgr = new HealthStatusManager();

    // Create a flagd instance with OpenTelemetry
    FlagdOptions options =
        FlagdOptions.builder()
            .withGlobalTelemetry(true)
            .build();

    FlagdProvider flagdProvider = new FlagdProvider(options);
    // Set flagd as the OpenFeature Provider
    OpenFeatureAPI.getInstance().setProvider(flagdProvider);
    
    // TLS configuration
    boolean tlsEnabled = Boolean.parseBoolean(
      Optional.ofNullable(System.getenv("AD_SERVICE_TLS_ENABLED"))
        .orElse(System.getProperty("AD_SERVICE_TLS_ENABLED", "false"))
    );

    SslContext sslContext = null;
    if (tlsEnabled) {
      String certPath = Optional.ofNullable(System.getenv("AD_SERVICE_TLS_CERT_PATH"))
        .orElse(System.getProperty("AD_SERVICE_TLS_CERT_PATH", ""));
      String keyPath = Optional.ofNullable(System.getenv("AD_SERVICE_TLS_KEY_PATH"))
        .orElse(System.getProperty("AD_SERVICE_TLS_KEY_PATH", ""));
      String clientCaPath = Optional.ofNullable(System.getenv("AD_SERVICE_TLS_CLIENT_CA_CERT_PATH"))
        .orElse(System.getProperty("AD_SERVICE_TLS_CLIENT_CA_CERT_PATH", ""));

      if (certPath.isEmpty() || keyPath.isEmpty()) {
        throw new RuntimeException("TLS enabled but required certificate/key path configuration is missing");
      }

      // Validate files exist and are readable
      validateTlsFile(certPath);
      validateTlsFile(keyPath);
      if (!clientCaPath.isEmpty()) {
        validateTlsFile(clientCaPath);
      }

      try {
        SslContextBuilder sslContextBuilder = GrpcSslContexts.forServer(new File(certPath), new File(keyPath));
        if (!clientCaPath.isEmpty()) {
          sslContextBuilder.trustManager(new File(clientCaPath))
            .clientAuth(ClientAuth.REQUIRE);
        }
        sslContext = sslContextBuilder.build();
      } catch (CertificateException | IllegalArgumentException e) {
        throw new RuntimeException("Invalid TLS configuration: " + e.getMessage(), e);
      }
    }

    NettyServerBuilder serverBuilder = NettyServerBuilder.forPort(port)
        .addService(new AdServiceImpl())
        .addService(healthMgr.getHealthService());

    if (tlsEnabled && sslContext != null) {
      serverBuilder.sslContext(sslContext);
    }

    server = serverBuilder.build().start();
    logger.info("Ad service started, listening on " + port);
    Runtime.getRuntime()
        .addShutdownHook(
            new Thread(
                () -> {
                  // Use stderr here since the logger may have been reset by its JVM shutdown hook.
                  System.err.println(
                      "*** shutting down gRPC ads server since JVM is shutting down");
                  AdService.this.stop();
                  System.err.println("*** server shut down");
                }));
    healthMgr.setStatus("", ServingStatus.SERVING);
    // Mark service as ready after all initialization is complete
    isReady = true;
    logger.info("Ad service initialized and ready to serve traffic");
  }

  private void stop() {
    // Get configured shutdown timeout
    int shutdownTimeoutSeconds = 10;
    String timeoutEnv = System.getenv("AD_SERVICE_SHUTDOWN_TIMEOUT_SECONDS");
    if (timeoutEnv != null && !timeoutEnv.isEmpty()) {
      try {
        shutdownTimeoutSeconds = Integer.parseInt(timeoutEnv);
        if (shutdownTimeoutSeconds < 1) {
          logger.warn("Invalid AD_SERVICE_SHUTDOWN_TIMEOUT_SECONDS value {}, using default 10s", shutdownTimeoutSeconds);
          shutdownTimeoutSeconds = 10;
        }
      } catch (NumberFormatException e) {
        logger.warn("Failed to parse AD_SERVICE_SHUTDOWN_TIMEOUT_SECONDS value '{}', using default 10s", timeoutEnv, e);
        shutdownTimeoutSeconds = 10;
      }
    }
    final int finalTimeout = shutdownTimeoutSeconds;
    int remainingRequests = inFlightRequests.get();
    logger.info("Shutdown initiated, timeout: {} seconds, remaining in-flight requests: {}", finalTimeout, remainingRequests);

    if (server != null) {
      // Step 1: Set health check to NOT_SERVING immediately
      healthMgr.setStatus("", ServingStatus.NOT_SERVING);
      // Also update readiness status
      isReady = false;
      
      // Step 2: Initiate graceful shutdown
      server.shutdown();
      
      try {
        // Step 3: Wait for in-flight requests to complete
        boolean terminated = server.awaitTermination(finalTimeout, java.util.concurrent.TimeUnit.SECONDS);
        remainingRequests = inFlightRequests.get();
        if (terminated) {
          logger.info("Successful graceful shutdown, remaining in-flight requests: {}", remainingRequests);
        } else {
          logger.info("Shutdown timeout of {} seconds reached, force terminating with {} remaining in-flight requests", finalTimeout, remainingRequests);
          server.shutdownNow();
        }
      } catch (InterruptedException e) {
        remainingRequests = inFlightRequests.get();
        logger.warn("Shutdown interrupted after {} seconds, force terminating with {} remaining in-flight requests", finalTimeout, remainingRequests, e);
        server.shutdownNow();
        Thread.currentThread().interrupt();
      }
    }

    // Step 4: Shut down prometheus metrics server gracefully
    if (prometheusServer != null) {
      logger.info("Shutting down prometheus metrics server");
      prometheusServer.stop();
    }
  }

  private enum AdRequestType {
    TARGETED,
    NOT_TARGETED
  }

  private enum AdResponseType {
    TARGETED,
    RANDOM
  }

  private static class AdServiceImpl extends oteldemo.AdServiceGrpc.AdServiceImplBase {
    
    private static final String AD_FAILURE = "adFailure";
    private static final String AD_MANUAL_GC_FEATURE_FLAG = "adManualGc";
    private static final String AD_HIGH_CPU_FEATURE_FLAG = "adHighCpu";
    private static final Client ffClient = OpenFeatureAPI.getInstance().getClient();
    
    // Validation constants
    private static final int MAX_CONTEXT_ENTRIES = 10;
    private static final int MAX_KEY_LENGTH = 100;
    private static final int MAX_VALUE_LENGTH = 100;
    
    private AdServiceImpl() {}

    /**
     * Retrieves ads based on context provided in the request {@code AdRequest}.
     *
     * @param req the request containing context.
     * @param responseObserver the stream observer which gets notified with the value of {@code
     *     AdResponse}
     */
    @Override
    public void getAds(AdRequest req, StreamObserver<AdResponse> responseObserver) {
      AdService service = AdService.getInstance();
      int inFlight = service.inFlightRequests.incrementAndGet();
      logger.debug("Incremented in-flight requests, current count: {}", inFlight);
      
      // get the current span in context
      Span span = Span.current();
      try {
        // Context keys processing
        List<String> contextKeys = req.getContextKeysList();

        List<Ad> allAds = new ArrayList<>();
        AdRequestType adRequestType;
        AdResponseType adResponseType;

        Baggage baggage = Baggage.fromContextOrNull(Context.current());
        MutableContext evaluationContext = new MutableContext();
        if (baggage != null) {
          final String sessionId = baggage.getEntryValue("session.id");
          span.setAttribute("session.id", sessionId);
          evaluationContext.setTargetingKey(sessionId);
          evaluationContext.add("session", sessionId);
          final String enduserId = baggage.getEntryValue("enduser.id");
          if (enduserId != null) {
            span.setAttribute("enduser.id", enduserId);
          }
        } else {
          logger.info("no baggage found in context");
        }

        CPULoad cpuload = CPULoad.getInstance();
        cpuload.execute(ffClient.getBooleanValue(AD_HIGH_CPU_FEATURE_FLAG, false, evaluationContext));

        span.setAttribute("demo.ad.context_keys", req.getContextKeysList().toString());
        span.setAttribute("demo.ad.context_keys.count", req.getContextKeysCount());
        if (req.getContextKeysCount() > 0) {
          logger.info("Targeted ad request received for " + req.getContextKeysList());
          for (int i = 0; i < req.getContextKeysCount(); i++) {
            Collection<Ad> ads = service.getAdsByCategory(req.getContextKeys(i));
            allAds.addAll(ads);
          }
          adRequestType = AdRequestType.TARGETED;
          adResponseType = AdResponseType.TARGETED;
        } else {
          logger.info("Non-targeted ad request received, preparing random response.");
          allAds = service.getRandomAds();
          adRequestType = AdRequestType.NOT_TARGETED;
          adResponseType = AdResponseType.RANDOM;
        }
        if (allAds.isEmpty()) {
          // Serve random ads.
          allAds = service.getRandomAds();
          adResponseType = AdResponseType.RANDOM;
        }
        span.setAttribute("demo.ad.count", allAds.size());
        span.setAttribute("demo.ad.request_type", adRequestType.name());
        span.setAttribute("demo.ad.response_type", adResponseType.name());

        adRequestsCounter.add(
            1,
            Attributes.of(
                adRequestTypeKey, adRequestType.name(), adResponseTypeKey, adResponseType.name()));

        // Throw 1/10 of the time to simulate a failure when the feature flag is enabled
        if (ffClient.getBooleanValue(AD_FAILURE, false, evaluationContext) && random.nextInt(10) == 0) {
          throw new StatusRuntimeException(Status.UNAVAILABLE);
        }

        if (ffClient.getBooleanValue(AD_MANUAL_GC_FEATURE_FLAG, false, evaluationContext)) {
          logger.warn("Feature Flag " + AD_MANUAL_GC_FEATURE_FLAG + " enabled, performing a manual gc now");
          GarbageCollectionTrigger gct = new GarbageCollectionTrigger();
          gct.doExecute();
        }

        AdResponse reply = AdResponse.newBuilder().addAllAds(allAds).build();
        responseObserver.onNext(reply);
        responseObserver.onCompleted();
      } catch (StatusRuntimeException e) {
        span.addEvent(
            "Error", Attributes.of(AttributeKey.stringKey("exception.message"), e.getMessage()));
        span.setStatus(StatusCode.ERROR);
        logger.log(Level.WARN, "GetAds Failed with status {}", e.getStatus());
        responseObserver.onError(e);
      } finally {
        int after = service.inFlightRequests.decrementAndGet();
        logger.debug("Decremented in-flight requests, current count: {}", after);
      }
    }
  }

  private static final ImmutableListMultimap<String, Ad> adsMap = createAdsMap();

  @WithSpan("getAdsByCategory")
  private Collection<Ad> getAdsByCategory(@SpanAttribute("demo.ad.category") String category) {
    Collection<Ad> ads = adsMap.get(category);
    Span.current().setAttribute("demo.ad.count", ads.size());
    adsServedCounter.labelValues(category).inc(ads.size());
    return ads;
  }

  private static final Random random = new Random();

  private List<Ad> getRandomAds() {

    List<Ad> ads = new ArrayList<>(MAX_ADS_TO_SERVE);

    // create and start a new span manually
    Span span = tracer.spanBuilder("getRandomAds").startSpan();

    // put the span into context, so if any child span is started the parent will be set properly
    try (Scope ignored = span.makeCurrent()) {

      Collection<Ad> allAds = adsMap.values();
      for (int i = 0; i < MAX_ADS_TO_SERVE; i++) {
        ads.add(Iterables.get(allAds, random.nextInt(allAds.size())));
      }
      span.setAttribute("demo.ad.count", ads.size());
      adsServedCounter.labelValues("random").inc(ads.size());

    } finally {
      span.end();
    }

    return ads;
  }

  private static AdService getInstance() {
    return service;
  }

  /** Await termination on the main thread since the grpc library uses daemon threads. */
  private void blockUntilShutdown() throws InterruptedException {
    if (server != null) {
      server.awaitTermination();
    }
  }

  private static ImmutableListMultimap<String, Ad> createAdsMap() {
    Ad binoculars =
        Ad.newBuilder()
            .setRedirectUrl("/product/2ZYFJ3GM2N")
            .setText("Roof Binoculars for sale. 50% off.")
            .build();
    Ad explorerTelescope =
        Ad.newBuilder()
            .setRedirectUrl("/product/66VCHSJNUP")
            .setText("Starsense Explorer Refractor Telescope for sale. 20% off.")
            .build();
    Ad colorImager =
        Ad.newBuilder()
            .setRedirectUrl("/product/0PUK6V6EV0")
            .setText("Solar System Color Imager for sale. 30% off.")
            .build();
    Ad opticalTube =
        Ad.newBuilder()
            .setRedirectUrl("/product/9SIQT8TOJO")
            .setText("Optical Tube Assembly for sale. 10% off.")
            .build();
    Ad travelTelescope =
        Ad.newBuilder()
            .setRedirectUrl("/product/1YMWWN1N4O")
            .setText(
                "Eclipsmart Travel Refractor Telescope for sale. Buy one, get second kit for free")
            .build();
    Ad solarFilter =
        Ad.newBuilder()
            .setRedirectUrl("/product/6E92ZMYYFZ")
            .setText("Solar Filter for sale. Buy two, get third one for free")
            .build();
    Ad cleaningKit =
        Ad.newBuilder()
            .setRedirectUrl("/product/L9ECAV7KIM")
            .setText("Lens Cleaning Kit for sale. Buy one, get second one for free")
            .build();
    return ImmutableListMultimap.<String, Ad>builder()
        .putAll("binoculars", binoculars)
        .putAll("telescopes", explorerTelescope)
        .putAll("accessories", colorImager, solarFilter, cleaningKit)
        .putAll("assembly", opticalTube)
        .putAll("travel", travelTelescope)
        // Keep the books category free of ads to ensure the random code branch is tested
        .build();
  }

  /** Main launches the server from the command line. */
  public static void main(String[] args) throws IOException, InterruptedException {
    // Start the RPC server. You shouldn't see any output from gRPC before this.
    logger.info("Ad service starting.");
    final AdService service = AdService.getInstance();
    service.start();
    service.blockUntilShutdown();
  }
}
