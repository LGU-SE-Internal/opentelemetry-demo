// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
import AsyncStorage from "@react-native-async-storage/async-storage";
import { BACKEND_BASE_URL } from '@/config/api';

const FRONTEND_PROXY_URL_SETTING = 'frontend_proxy_url';

export const getFrontendProxyURL = async (): Promise<string> => {
  const proxyURL = await AsyncStorage.getItem(FRONTEND_PROXY_URL_SETTING);
  if (proxyURL) {
    return proxyURL
  } else {
    return BACKEND_BASE_URL;
  }
};

export const setFrontendProxyURL = async (url: string) => {
  await AsyncStorage.setItem(FRONTEND_PROXY_URL_SETTING, url);
}

export default getFrontendProxyURL;
