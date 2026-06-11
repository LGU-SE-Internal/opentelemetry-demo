module github.com/open-telemetry/opentelemetry-demo/src/adservice

go 1.25.0

require (
	github.com/lib/pq v1.10.9
	go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc v0.46.1
	google.golang.org/grpc v1.60.1
	google.golang.org/protobuf v1.32.0
)

require golang.org/x/time v0.15.0 // indirect

replace github.com/open-telemetry/opentelemetry-demo/pb => ../../pb
