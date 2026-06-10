/**
 * Validates a provided backend base URL
 * @param url - The URL string to validate
 * @returns Normalized URL string (with trailing slash removed if present)
 * @throws {Error} "Invalid backend URL: must start with http:// or https://" if scheme is missing/invalid
 * @throws {Error} "Invalid backend URL: host is required" if no host is provided
 */
export function validateBackendUrl(url: string): string {
  // Check scheme
  if (!url.startsWith('http://') && !url.startsWith('https://')) {
    throw new Error("Invalid backend URL: must start with http:// or https://");
  }

  // Parse URL to check host
  let parsedUrl: URL;
  try {
    parsedUrl = new URL(url);
  } catch (e) {
    throw new Error("Invalid backend URL: host is required");
  }

  if (!parsedUrl.host) {
    throw new Error("Invalid backend URL: host is required");
  }

  // Remove trailing slash if present
  return url.replace(/\/$/, '');
}
