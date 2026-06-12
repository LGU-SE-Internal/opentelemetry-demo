// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

import { Ad, Address, Cart, CartItem, Money, PlaceOrderRequest, Product, ProductReview } from '../protos/demo';
import { IProductCart, IProductCartItem, IProductCheckout } from '../types/Cart';
import { AttributeNames } from '../utils/enums/AttributeNames';
import SessionGateway from './Session.gateway';
import { context, propagation } from "@opentelemetry/api";
import { createEnhancedGatewayClient } from './EnhancedGatewayClient';

const { userId } = SessionGateway.getSession();

const basePath = '/api';

const enhancedClient = createEnhancedGatewayClient(basePath, 'api-gateway');

const Apis = () => ({
  getCart(currencyCode: string) {
    return enhancedClient.request<IProductCart>({
      url: `/cart`,
      queryParams: { sessionId: userId, currencyCode },
    });
  },
  addCartItem({ currencyCode, ...item }: CartItem & { currencyCode: string }) {
    return enhancedClient.request<Cart>({
      url: `/cart`,
      body: { item, userId },
      queryParams: { currencyCode },
      method: 'POST',
    });
  },
  emptyCart() {
    return enhancedClient.request<undefined>({
      url: `/cart`,
      method: 'DELETE',
      body: { userId },
    });
  },

  getSupportedCurrencyList() {
    return enhancedClient.request<string[]>({
      url: `/currency`,
    });
  },

  getShippingCost(itemList: IProductCartItem[], currencyCode: string, address: Address) {
    return enhancedClient.request<Money>({
      url: `/shipping`,
      queryParams: {
        itemList: JSON.stringify(itemList.map(({ productId, quantity }) => ({ productId, quantity }))),
        currencyCode,
        address: JSON.stringify(address),
      },
    });
  },

  placeOrder({ currencyCode, ...order }: PlaceOrderRequest & { currencyCode: string }) {
    return enhancedClient.request<IProductCheckout>({
      url: `/checkout`,
      method: 'POST',
      queryParams: { currencyCode },
      body: order,
    });
  },

  listProducts(currencyCode: string) {
    return enhancedClient.request<Product[]>({
      url: `/products`,
      queryParams: { currencyCode },
    });
  },
  getProduct(productId: string, currencyCode: string) {
    return enhancedClient.request<Product>({
      url: `/products/${productId}`,
      queryParams: { currencyCode },
    });
  },
  getProductReviews(productId: string) {
    return enhancedClient.request<ProductReview[]>({
      url: `/product-reviews/${productId}`
    });
  },
  getAverageProductReviewScore(productId: string) {
    return enhancedClient.request<string>({
      url: `/product-reviews-avg-score/${productId}`
    });
  },
  askProductAIAssistant(productId: string, question: string) {
    return enhancedClient.request<string>({
      url: `/product-ask-ai-assistant/${productId}`,
      method: 'POST',
      body: { question },
    });
  },
  listRecommendations(productIds: string[], currencyCode: string) {
    return enhancedClient.request<Product[]>({
      url: `/recommendations`,
      queryParams: {
        productIds,
        sessionId: userId,
        currencyCode,
      },
    });
  },
  listAds(contextKeys: string[]) {
    return enhancedClient.request<Ad[]>({
      url: `/data`,
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
