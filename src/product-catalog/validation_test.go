package main

import (
	"context"
	"strings"
	"testing"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	"github.com/opentelemetry/opentelemetry-demo/src/product-catalog/genproto/oteldemo"
)

// TestAC1_GetProductEmptyId verifies GetProduct returns InvalidArgument for empty id
func TestAC1_GetProductEmptyId(t *testing.T) {
	conn, err := grpc.DialContext(context.Background(), "localhost:3550", grpc.WithInsecure())
	if err != nil {
		t.Fatalf("failed to dial: %v", err)
	}
	defer conn.Close()
	client := oteldemo.NewProductCatalogServiceClient(conn)

	req := &oteldemo.GetProductRequest{Id: ""}
	_, err = client.GetProduct(context.Background(), req)

	if err == nil {
		t.Fatalf("expected error for empty id, got nil")
	}
	st, ok := status.FromError(err)
	if !ok {
		t.Fatalf("expected gRPC status error, got %v", err)
	}
	if st.Code() != codes.InvalidArgument {
		t.Fatalf("expected InvalidArgument code, got %v", st.Code())
	}
	if !strings.Contains(st.Message(), "id") {
		t.Fatalf("expected error message to mention 'id' field, got %s", st.Message())
	}
}

// TestAC2_GetProductNonAlphanumericId verifies GetProduct returns InvalidArgument for id with non-alphanumeric chars
func TestAC2_GetProductNonAlphanumericId(t *testing.T) {
	conn, err := grpc.DialContext(context.Background(), "localhost:3550", grpc.WithInsecure())
	if err != nil {
		t.Fatalf("failed to dial: %v", err)
	}
	defer conn.Close()
	client := oteldemo.NewProductCatalogServiceClient(conn)

	testCases := []string{"prod-123!", "prod@456", "prod#789", "prod$0ab", "prod cde"}
	for _, tc := range testCases {
		t.Run(tc, func(t *testing.T) {
			req := &oteldemo.GetProductRequest{Id: tc}
			_, err = client.GetProduct(context.Background(), req)

			if err == nil {
				t.Fatalf("expected error for id %q, got nil", tc)
			}
			st, ok := status.FromError(err)
			if !ok {
				t.Fatalf("expected gRPC status error, got %v", err)
			}
			if st.Code() != codes.InvalidArgument {
				t.Fatalf("expected InvalidArgument code for id %q, got %v", tc, st.Code())
			}
		})
	}
}

// TestAC3_GetProductIdTooLong verifies GetProduct returns InvalidArgument for id longer than 64 chars
func TestAC3_GetProductIdTooLong(t *testing.T) {
	conn, err := grpc.DialContext(context.Background(), "localhost:3550", grpc.WithInsecure())
	if err != nil {
		t.Fatalf("failed to dial: %v", err)
	}
	defer conn.Close()
	client := oteldemo.NewProductCatalogServiceClient(conn)

	longId := strings.Repeat("a", 65)
	req := &oteldemo.GetProductRequest{Id: longId}
	_, err = client.GetProduct(context.Background(), req)

	if err == nil {
		t.Fatalf("expected error for id longer than 64 chars, got nil")
	}
	st, ok := status.FromError(err)
	if !ok {
		t.Fatalf("expected gRPC status error, got %v", err)
	}
	if st.Code() != codes.InvalidArgument {
		t.Fatalf("expected InvalidArgument code, got %v", st.Code())
	}
}

// TestAC4_ListProductsPageSizeLessThan1 verifies ListProducts returns InvalidArgument for page_size <1
func TestAC4_ListProductsPageSizeLessThan1(t *testing.T) {
	conn, err := grpc.DialContext(context.Background(), "localhost:3550", grpc.WithInsecure())
	if err != nil {
		t.Fatalf("failed to dial: %v", err)
	}
	defer conn.Close()
	client := oteldemo.NewProductCatalogServiceClient(conn)

	req := &oteldemo.ListProductsRequest{PageSize: 0, PageToken: 1}
	_, err = client.ListProducts(context.Background(), req)

	if err == nil {
		t.Fatalf("expected error for page_size 0, got nil")
	}
	st, ok := status.FromError(err)
	if !ok {
		t.Fatalf("expected gRPC status error, got %v", err)
	}
	if st.Code() != codes.InvalidArgument {
		t.Fatalf("expected InvalidArgument code, got %v", st.Code())
	}
	if !strings.Contains(st.Message(), "page_size") {
		t.Fatalf("expected error message to mention 'page_size' field, got %s", st.Message())
	}
}

// TestAC5_ListProductsPageSizeGreaterThan100 verifies ListProducts returns InvalidArgument for page_size >100
func TestAC5_ListProductsPageSizeGreaterThan100(t *testing.T) {
	conn, err := grpc.DialContext(context.Background(), "localhost:3550", grpc.WithInsecure())
	if err != nil {
		t.Fatalf("failed to dial: %v", err)
	}
	defer conn.Close()
	client := oteldemo.NewProductCatalogServiceClient(conn)

	req := &oteldemo.ListProductsRequest{PageSize: 101, PageToken: 1}
	_, err = client.ListProducts(context.Background(), req)

	if err == nil {
		t.Fatalf("expected error for page_size 101, got nil")
	}
	st, ok := status.FromError(err)
	if !ok {
		t.Fatalf("expected gRPC status error, got %v", err)
	}
	if st.Code() != codes.InvalidArgument {
		t.Fatalf("expected InvalidArgument code, got %v", st.Code())
	}
}

// TestAC6_ListProductsPageTokenLessThan1 verifies ListProducts returns InvalidArgument for page_token <1
func TestAC6_ListProductsPageTokenLessThan1(t *testing.T) {
	conn, err := grpc.DialContext(context.Background(), "localhost:3550", grpc.WithInsecure())
	if err != nil {
		t.Fatalf("failed to dial: %v", err)
	}
	defer conn.Close()
	client := oteldemo.NewProductCatalogServiceClient(conn)

	req := &oteldemo.ListProductsRequest{PageSize: 10, PageToken: 0}
	_, err = client.ListProducts(context.Background(), req)

	if err == nil {
		t.Fatalf("expected error for page_token 0, got nil")
	}
	st, ok := status.FromError(err)
	if !ok {
		t.Fatalf("expected gRPC status error, got %v", err)
	}
	if st.Code() != codes.InvalidArgument {
		t.Fatalf("expected InvalidArgument code, got %v", st.Code())
	}
	if !strings.Contains(st.Message(), "page_token") {
		t.Fatalf("expected error message to mention 'page_token' field, got %s", st.Message())
	}
}

// TestAC7_SearchProductsQueryTooLong verifies SearchProducts returns InvalidArgument for query longer than 256 chars
func TestAC7_SearchProductsQueryTooLong(t *testing.T) {
	conn, err := grpc.DialContext(context.Background(), "localhost:3550", grpc.WithInsecure())
	if err != nil {
		t.Fatalf("failed to dial: %v", err)
	}
	defer conn.Close()
	client := oteldemo.NewProductCatalogServiceClient(conn)

	longQuery := strings.Repeat("a", 257)
	req := &oteldemo.SearchProductsRequest{Query: longQuery, PageSize: 10, PageToken: 1}
	_, err = client.SearchProducts(context.Background(), req)

	if err == nil {
		t.Fatalf("expected error for query longer than 256 chars, got nil")
	}
	st, ok := status.FromError(err)
	if !ok {
		t.Fatalf("expected gRPC status error, got %v", err)
	}
	if st.Code() != codes.InvalidArgument {
		t.Fatalf("expected InvalidArgument code, got %v", st.Code())
	}
	if !strings.Contains(st.Message(), "query") {
		t.Fatalf("expected error message to mention 'query' field, got %s", st.Message())
	}
}

// TestAC8_SearchProductsPageSizeLessThan1 verifies SearchProducts returns InvalidArgument for page_size <1
func TestAC8_SearchProductsPageSizeLessThan1(t *testing.T) {
	conn, err := grpc.DialContext(context.Background(), "localhost:3550", grpc.WithInsecure())
	if err != nil {
		t.Fatalf("failed to dial: %v", err)
	}
	defer conn.Close()
	client := oteldemo.NewProductCatalogServiceClient(conn)

	req := &oteldemo.SearchProductsRequest{Query: "test", PageSize: 0, PageToken: 1}
	_, err = client.SearchProducts(context.Background(), req)

	if err == nil {
		t.Fatalf("expected error for page_size 0, got nil")
	}
	st, ok := status.FromError(err)
	if !ok {
		t.Fatalf("expected gRPC status error, got %v", err)
	}
	if st.Code() != codes.InvalidArgument {
		t.Fatalf("expected InvalidArgument code, got %v", st.Code())
	}
}

// TestAC9_SearchProductsPageSizeGreaterThan100 verifies SearchProducts returns InvalidArgument for page_size >100
func TestAC9_SearchProductsPageSizeGreaterThan100(t *testing.T) {
	conn, err := grpc.DialContext(context.Background(), "localhost:3550", grpc.WithInsecure())
	if err != nil {
		t.Fatalf("failed to dial: %v", err)
	}
	defer conn.Close()
	client := oteldemo.NewProductCatalogServiceClient(conn)

	req := &oteldemo.SearchProductsRequest{Query: "test", PageSize: 101, PageToken: 1}
	_, err = client.SearchProducts(context.Background(), req)

	if err == nil {
		t.Fatalf("expected error for page_size 101, got nil")
	}
	st, ok := status.FromError(err)
	if !ok {
		t.Fatalf("expected gRPC status error, got %v", err)
	}
	if st.Code() != codes.InvalidArgument {
		t.Fatalf("expected InvalidArgument code, got %v", st.Code())
	}
}

// TestAC10_SearchProductsPageTokenLessThan1 verifies SearchProducts returns InvalidArgument for page_token <1
func TestAC10_SearchProductsPageTokenLessThan1(t *testing.T) {
	conn, err := grpc.DialContext(context.Background(), "localhost:3550", grpc.WithInsecure())
	if err != nil {
		t.Fatalf("failed to dial: %v", err)
	}
	defer conn.Close()
	client := oteldemo.NewProductCatalogServiceClient(conn)

	req := &oteldemo.SearchProductsRequest{Query: "test", PageSize: 10, PageToken: 0}
	_, err = client.SearchProducts(context.Background(), req)

	if err == nil {
		t.Fatalf("expected error for page_token 0, got nil")
	}
	st, ok := status.FromError(err)
	if !ok {
		t.Fatalf("expected gRPC status error, got %v", err)
	}
	if st.Code() != codes.InvalidArgument {
		t.Fatalf("expected InvalidArgument code, got %v", st.Code())
	}
}

// TestAC11_ValidRequestsAreForwarded verifies valid requests pass validation
func TestAC11_ValidRequestsAreForwarded(t *testing.T) {
	conn, err := grpc.DialContext(context.Background(), "localhost:3550", grpc.WithInsecure())
	if err != nil {
		t.Fatalf("failed to dial: %v", err)
	}
	defer conn.Close()
	client := oteldemo.NewProductCatalogServiceClient(conn)

	t.Run("GetProduct valid id", func(t *testing.T) {
		req := &oteldemo.GetProductRequest{Id: "abc123XYZ"}
		_, err = client.GetProduct(context.Background(), req)
		// If product exists, should return OK or NotFound, NOT InvalidArgument
		if err != nil {
			st, ok := status.FromError(err)
			if ok && st.Code() == codes.InvalidArgument {
				t.Fatalf("unexpected InvalidArgument for valid id: %v", err)
			}
		}
	})

	t.Run("ListProducts valid params", func(t *testing.T) {
		req := &oteldemo.ListProductsRequest{PageSize: 50, PageToken: 2}
		_, err = client.ListProducts(context.Background(), req)
		if err != nil {
			st, ok := status.FromError(err)
			if ok && st.Code() == codes.InvalidArgument {
				t.Fatalf("unexpected InvalidArgument for valid list params: %v", err)
			}
		}
	})

	t.Run("SearchProducts valid params", func(t *testing.T) {
		req := &oteldemo.SearchProductsRequest{Query: "shoes", PageSize: 10, PageToken: 1}
		_, err = client.SearchProducts(context.Background(), req)
		if err != nil {
			st, ok := status.FromError(err)
			if ok && st.Code() == codes.InvalidArgument {
				t.Fatalf("unexpected InvalidArgument for valid search params: %v", err)
			}
		}
	})
}
