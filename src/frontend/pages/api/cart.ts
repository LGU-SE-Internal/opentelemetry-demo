// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

import type { NextApiHandler } from 'next';
import CartGateway from '../../gateways/rpc/Cart.gateway';
import { AddItemRequest, Empty } from '../../protos/demo';
import ProductCatalogService from '../../services/ProductCatalog.service';
import { IProductCart, IProductCartItem } from '../../types/Cart';
import InstrumentationMiddleware from '../../utils/telemetry/InstrumentationMiddleware';
import { postCartSchema, getCartSchema, deleteCartItemSchema, formatValidationError } from '../../utils/validation';
import { z } from 'zod';

type TResponse = IProductCart | Empty | ReturnType<typeof formatValidationError>;

const handler: NextApiHandler<TResponse> = async ({ method, body, query }, res) => {
  switch (method) {
    case 'GET': {
      const validationResult = getCartSchema.safeParse(query);
      if (!validationResult.success) {
        return res.status(400).json(formatValidationError(validationResult.error));
      }

      const { sessionId = '', currencyCode = '' } = query;
      const { userId, items } = await CartGateway.getCart(sessionId as string);

      const productList: IProductCartItem[] = await Promise.all(
        items.map(async ({ productId, quantity }) => {
          const product = await ProductCatalogService.getProduct(productId, currencyCode as string);

          return {
            productId,
            quantity,
            product,
          };
        })
      );

      return res.status(200).json({ userId, items: productList });
    }

    case 'POST': {
      const validationResult = postCartSchema.safeParse(body);
      if (!validationResult.success) {
        return res.status(400).json(formatValidationError(validationResult.error));
      }

      const { userId } = body as AddItemRequest;
      const item = validationResult.data;

      await CartGateway.addItem(userId, item);
      const cart = await CartGateway.getCart(userId);

      return res.status(200).json(cart);
    }

    case 'DELETE': {
      if (body && body.productId) {
        const validationResult = deleteCartItemSchema.safeParse(body);
        if (!validationResult.success) {
          return res.status(400).json(formatValidationError(validationResult.error));
        }
        // Remove item from cart logic would go here if implemented
      }

      const { userId } = body as AddItemRequest;
      await CartGateway.emptyCart(userId);

      return res.status(200).json({ userId, items: [] });
    }

    default: {
      return res.status(405);
    }
  }
};

export default InstrumentationMiddleware(handler);

