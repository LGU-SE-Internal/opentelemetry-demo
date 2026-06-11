// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

import { ChannelCredentials } from '@grpc/grpc-js';
import { Cart, CartItem, CartServiceClient, Empty } from '../../protos/demo';
import { withGrpcRetry } from '../../utils/grpcRetry';

const { CART_ADDR = '' } = process.env;

const client = new CartServiceClient(CART_ADDR, ChannelCredentials.createInsecure());

const CartGateway = () => ({
  getCart(userId: string) {
    return withGrpcRetry('cart', 'GetCart', () => new Promise<Cart>((resolve, reject) =>
      client.getCart({ userId }, (error, response) => (error ? reject(error) : resolve(response)))
    ), true);
  },
  addItem(userId: string, item: CartItem) {
    return withGrpcRetry('cart', 'AddItem', () => new Promise<Empty>((resolve, reject) =>
      client.addItem({ userId, item }, (error, response) => (error ? reject(error) : resolve(response)))
    ), false);
  },
  emptyCart(userId: string) {
    return withGrpcRetry('cart', 'EmptyCart', () => new Promise<Empty>((resolve, reject) =>
      client.emptyCart({ userId }, (error, response) => (error ? reject(error) : resolve(response)))
    ), false);
  },
});

export default CartGateway();
