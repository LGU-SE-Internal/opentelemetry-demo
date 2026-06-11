// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

import { Ad, Address, Cart, CartItem, Money, PlaceOrderRequest, Product, ProductReview } from '../protos/demo';
import { IProductCart, IProductCartItem, IProductCheckout } from '../types/Cart';
import request from '../utils/Request';
import { AttributeNames } from '../utils/enums/AttributeNames';
import SessionGateway from './Session.gateway';
import { context, propagation } from "@opentelemetry/api";
import { withGrpcRetry } from '../utils/grpcRetry';

const { userId } = SessionGateway.getSession();

const basePath = '/api';

const Apis = () => ({
  getCart(currencyCode: string) {
    return withGrpcRetry(
      () => request<IProductCart>({
        url: `${basePath}/cart`,
        queryParams: { sessionId: userId, currencyCode },
      }),
      'oteldemo.CartService/GetCart'
    );
  },
  addCartItem({ currencyCode, ...item }: CartItem & { currencyCode: string }) {
    return request<Cart>({
      url: `${basePath}/cart`,
      body: { item, userId },
      queryParams: { currencyCode },
      method: 'POST',
    });
  },
  emptyCart() {
    return request<undefined>({
      url: `${basePath}/cart`,
      method: 'DELETE',
      body: { userId },
    });
  },

  getSupportedCurrencyList() {
    return request<string[]>({
      url: `${basePath}/currency`,
    });
  },

  getShippingCost(itemList: IProductCartItem[], currencyCode: string, address: Address) {
    return withGrpcRetry(
      () => request<Money>({
        url: `${basePath}/shipping`,
        queryParams: {
          itemList: JSON.stringify(itemList.map(({ productId, quantity }) => ({ productId, quantity }))),
          currencyCode,
          address: JSON.stringify(address),
        },
      }),
      'oteldemo.ShippingService/GetQuote'
    );
  },

  placeOrder({ currencyCode, ...order }: PlaceOrderRequest & { currencyCode: string }) {
    return request<IProductCheckout>({
      url: `${basePath}/checkout`,
      method: 'POST',
      queryParams: { currencyCode },
      body: order,
    });
  },

  listProducts(currencyCode: string) {
    return withGrpcRetry(
      () => request<Product[]>({
        url: `${basePath}/products`,
        queryParams: { currencyCode },
      }),
      'oteldemo.ProductCatalogService/ListProducts'
    );
  },
  getProduct(productId: string, currencyCode: string) {
    return withGrpcRetry(
      () => request<Product>({
        url: `${basePath}/products/${productId}`,
        queryParams: { currencyCode },
      }),
      'oteldemo.ProductCatalogService/GetProduct'
    );
  },
  getProductReviews(productId: string) {
    return request<ProductReview[]>({
      url: `${basePath}/product-reviews/${productId}`
    });
  },
  getAverageProductReviewScore(productId: string) {
    return request<string>({
      url: `${basePath}/product-reviews-avg-score/${productId}`
    });
  },
  askProductAIAssistant(productId: string, question: string) {
    return request<string>({
      url: `${basePath}/product-ask-ai-assistant/${productId}`,
      method: 'POST',
      body: { question },
    });
  },
  listRecommendations(productIds: string[], currencyCode: string) {
    return request<Product[]>({
      url: `${basePath}/recommendations`,
      queryParams: {
        productIds,
        sessionId: userId,
        currencyCode,
      },
    });
  },
  listAds(contextKeys: string[]) {
    return request<Ad[]>({
      url: `${basePath}/data`,
      queryParams: {
        contextKeys,
      },
    });
  },
});

/**
 * Extends all the API calls to set baggage automatically.
 */
const ApiGateway = new Proxy(Apis(), {
  get(target, prop, receiver) {
    const originalFunction = Reflect.get(target, prop, receiver);

    if (typeof originalFunction !== 'function') {
      return originalFunction;
    }

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return function (...args: any[]) {
      const baggage = propagation.getActiveBaggage() || propagation.createBaggage();
      const newBaggage = baggage
        .setEntry(AttributeNames.SESSION_ID, { value: userId })
        .setEntry(AttributeNames.ENDUSER_ID, { value: userId });
      const newContext = propagation.setBaggage(context.active(), newBaggage);
      return context.with(newContext, () => {
        return Reflect.apply(originalFunction, undefined, args);
      });
    };
  },
});

export default ApiGateway;
