// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

import type { NextApiHandler } from 'next';
import ProductCatalogService from '../../../services/ProductCatalog.service';
import InstrumentationMiddleware from '../../../utils/telemetry/InstrumentationMiddleware';
import { getProductsSchema, formatValidationError } from '../../../utils/validation';

const handler: NextApiHandler = async ({ query }, res) => {
  const validationResult = getProductsSchema.safeParse(query);
  if (!validationResult.success) {
    return res.status(400).json(formatValidationError(validationResult.error));
  }

  const { currencyCode = '', search, page, limit } = query;
  const products = await ProductCatalogService.listProducts(
    currencyCode as string,
    search as string | undefined,
    Number(page),
    Number(limit)
  );

  return res.status(200).json(products);
};

export default InstrumentationMiddleware(handler);
