// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

import type { NextApiRequest, NextApiResponse } from 'next';
import InstrumentationMiddleware from '../../utils/telemetry/InstrumentationMiddleware';
import RecommendationsGateway from '../../gateways/rpc/Recommendations.gateway';
import { Empty, Product } from '../../protos/demo';
import ProductCatalogService from '../../services/ProductCatalog.service';
import { getRecommendationsSchema, formatValidationError } from '../../utils/validation';

type TResponse = Product[] | Empty | ReturnType<typeof formatValidationError>;

const handler = async ({ method, query }: NextApiRequest, res: NextApiResponse<TResponse>) => {
  switch (method) {
    case 'GET': {
      const validationResult = getRecommendationsSchema.safeParse(query);
      if (!validationResult.success) {
        return res.status(400).json(formatValidationError(validationResult.error));
      }

      const { productId, limit = 5, sessionId = '', currencyCode = '' } = query;
      const { productIds: productList } = await RecommendationsGateway.listRecommendations(
        sessionId as string,
        [productId as string]
      );
      const recommendedProductList = await Promise.all(
        productList.slice(0, Number(limit)).map(id => ProductCatalogService.getProduct(id, currencyCode as string))
      );

      return res.status(200).json(recommendedProductList);
    }

    default: {
      return res.status(405).send('');
    }
  }
};

export default InstrumentationMiddleware(handler);
