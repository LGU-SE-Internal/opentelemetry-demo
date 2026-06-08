using System.Threading.Tasks;
using Grpc.Core;
using Moq;
using Otel.Demo.CartService.V1;
using Xunit;
using OpenFeature;
using cart.cartstore;
using cart.services;

namespace Otel.Demo.CartService.Tests;

public class CartServiceValidationInputTests
{
    private const string ValidUserId = "test-user-123";
    private const string ValidProductId = "product-456";
    private const int ValidQuantity = 5;
    
    private readonly Mock<ICartStore> _mockCartStore;
    private readonly CartService _cartService;

    public CartServiceValidationInputTests()
    {
        _mockCartStore = new Mock<ICartStore>();
        var mockBadCartStore = new Mock<ICartStore>();
        var mockFeatureClient = new Mock<IFeatureClient>();
        _cartService = new CartService(_mockCartStore.Object, mockBadCartStore.Object, mockFeatureClient.Object);
    }

    [Fact]
    public async Task test_ac1_GetCart_EmptyUserId_ReturnsInvalidArgument()
    {
        // Arrange
        var request = new GetCartRequest { UserId = "" };

        // Act & Assert
        var exception = await Assert.ThrowsAsync<RpcException>(() => _cartService.GetCart(request, null!));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Equal("User ID must not be empty or contain only whitespace", exception.Status.Detail);
        _mockCartStore.Verify(s => s.GetCartAsync(It.IsAny<string>()), Times.Never);
    }

    [Fact]
    public async Task test_ac1_GetCart_WhitespaceUserId_ReturnsInvalidArgument()
    {
        // Arrange
        var request = new GetCartRequest { UserId = "   \t\n  " };

        // Act & Assert
        var exception = await Assert.ThrowsAsync<RpcException>(() => _cartService.GetCart(request, null!));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Equal("User ID must not be empty or contain only whitespace", exception.Status.Detail);
        _mockCartStore.Verify(s => s.GetCartAsync(It.IsAny<string>()), Times.Never);
    }

    [Fact]
    public async Task test_ac2_EmptyCart_EmptyUserId_ReturnsInvalidArgument()
    {
        // Arrange
        var request = new EmptyCartRequest { UserId = "" };

        // Act & Assert
        var exception = await Assert.ThrowsAsync<RpcException>(() => _cartService.EmptyCart(request, null!));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Equal("User ID must not be empty or contain only whitespace", exception.Status.Detail);
        _mockCartStore.Verify(s => s.EmptyCartAsync(It.IsAny<string>()), Times.Never);
    }

    [Fact]
    public async Task test_ac2_EmptyCart_WhitespaceUserId_ReturnsInvalidArgument()
    {
        // Arrange
        var request = new EmptyCartRequest { UserId = "  \r  " };

        // Act & Assert
        var exception = await Assert.ThrowsAsync<RpcException>(() => _cartService.EmptyCart(request, null!));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Equal("User ID must not be empty or contain only whitespace", exception.Status.Detail);
        _mockCartStore.Verify(s => s.EmptyCartAsync(It.IsAny<string>()), Times.Never);
    }

    [Fact]
    public async Task test_ac3_AddItem_EmptyUserId_ReturnsInvalidArgument()
    {
        // Arrange
        var request = new AddItemRequest { UserId = "", ProductId = ValidProductId, Quantity = ValidQuantity };

        // Act & Assert
        var exception = await Assert.ThrowsAsync<RpcException>(() => _cartService.AddItem(request, null!));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Equal("User ID must not be empty or contain only whitespace", exception.Status.Detail);
        _mockCartStore.Verify(s => s.AddItemAsync(It.IsAny<string>(), It.IsAny<CartItem>()), Times.Never);
    }

    [Fact]
    public async Task test_ac4_AddItem_EmptyProductId_ReturnsInvalidArgument()
    {
        // Arrange
        var request = new AddItemRequest { UserId = ValidUserId, ProductId = "", Quantity = ValidQuantity };

        // Act & Assert
        var exception = await Assert.ThrowsAsync<RpcException>(() => _cartService.AddItem(request, null!));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Equal("Product ID must not be empty or contain only whitespace", exception.Status.Detail);
        _mockCartStore.Verify(s => s.AddItemAsync(It.IsAny<string>(), It.IsAny<CartItem>()), Times.Never);
    }

    [Fact]
    public async Task test_ac4_AddItem_WhitespaceProductId_ReturnsInvalidArgument()
    {
        // Arrange
        var request = new AddItemRequest { UserId = ValidUserId, ProductId = " \t ", Quantity = ValidQuantity };

        // Act & Assert
        var exception = await Assert.ThrowsAsync<RpcException>(() => _cartService.AddItem(request, null!));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Equal("Product ID must not be empty or contain only whitespace", exception.Status.Detail);
        _mockCartStore.Verify(s => s.AddItemAsync(It.IsAny<string>(), It.IsAny<CartItem>()), Times.Never);
    }

    [Fact]
    public async Task test_ac5_AddItem_ZeroQuantity_ReturnsInvalidArgument()
    {
        // Arrange
        var request = new AddItemRequest { UserId = ValidUserId, ProductId = ValidProductId, Quantity = 0 };

        // Act & Assert
        var exception = await Assert.ThrowsAsync<RpcException>(() => _cartService.AddItem(request, null!));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Equal("Quantity must be a positive integer greater than 0", exception.Status.Detail);
        _mockCartStore.Verify(s => s.AddItemAsync(It.IsAny<string>(), It.IsAny<CartItem>()), Times.Never);
    }

    [Fact]
    public async Task test_ac5_AddItem_NegativeQuantity_ReturnsInvalidArgument()
    {
        // Arrange
        var request = new AddItemRequest { UserId = ValidUserId, ProductId = ValidProductId, Quantity = -5 };

        // Act & Assert
        var exception = await Assert.ThrowsAsync<RpcException>(() => _cartService.AddItem(request, null!));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Equal("Quantity must be a positive integer greater than 0", exception.Status.Detail);
        _mockCartStore.Verify(s => s.AddItemAsync(It.IsAny<string>(), It.IsAny<CartItem>()), Times.Never);
    }

    [Fact]
    public async Task test_ac6_AddItem_ExceedMaxQuantity_ReturnsInvalidArgument()
    {
        // Arrange
        var request = new AddItemRequest { UserId = ValidUserId, ProductId = ValidProductId, Quantity = 101 };

        // Act & Assert
        var exception = await Assert.ThrowsAsync<RpcException>(() => _cartService.AddItem(request, null!));
        Assert.Equal(StatusCode.InvalidArgument, exception.StatusCode);
        Assert.Equal("Quantity must not exceed maximum allowed value of 100", exception.Status.Detail);
        _mockCartStore.Verify(s => s.AddItemAsync(It.IsAny<string>(), It.IsAny<CartItem>()), Times.Never);
    }

    [Fact]
    public async Task test_ac7_GetCart_ValidRequest_ForwardsToStore()
    {
        // Arrange
        var request = new GetCartRequest { UserId = ValidUserId };
        var expectedCart = new Cart { UserId = ValidUserId };
        _mockCartStore.Setup(s => s.GetCartAsync(ValidUserId)).ReturnsAsync(expectedCart);

        // Act
        var response = await _cartService.GetCart(request, null!);

        // Assert
        Assert.Equal(expectedCart, response);
        _mockCartStore.Verify(s => s.GetCartAsync(ValidUserId), Times.Once);
    }

    [Fact]
    public async Task test_ac7_EmptyCart_ValidRequest_ForwardsToStore()
    {
        // Arrange
        var request = new EmptyCartRequest { UserId = ValidUserId };
        _mockCartStore.Setup(s => s.EmptyCartAsync(ValidUserId)).Returns(Task.CompletedTask);

        // Act
        await _cartService.EmptyCart(request, null!);

        // Assert
        _mockCartStore.Verify(s => s.EmptyCartAsync(ValidUserId), Times.Once);
    }

    [Fact]
    public async Task test_ac7_AddItem_ValidRequest_ForwardsToStore()
    {
        // Arrange
        var request = new AddItemRequest { UserId = ValidUserId, ProductId = ValidProductId, Quantity = 100 };
        var expectedCart = new Cart { UserId = ValidUserId };
        _mockCartStore.Setup(s => s.AddItemAsync(ValidUserId, It.Is<CartItem>(i => i.ProductId == ValidProductId && i.Quantity == 100))).ReturnsAsync(expectedCart);

        // Act
        var response = await _cartService.AddItem(request, null!);

        // Assert
        Assert.Equal(expectedCart, response);
        _mockCartStore.Verify(s => s.AddItemAsync(ValidUserId, It.Is<CartItem>(i => i.ProductId == ValidProductId && i.Quantity == 100)), Times.Once);
    }
}
