// src/index.js
export default {
  async fetch(request) {
    const requestUrl = new URL(request.url);

    // 1. Determine Target URL (Query Param takes precedence over Path)
    let targetUrlParam = requestUrl.searchParams.get("url");
    let targetUrl = targetUrlParam 
      ? decodeURIComponent(targetUrlParam)
      : requestUrl.href.substring(requestUrl.origin.length + 1);

    if (!targetUrl) {
      return new Response("Missing target URL", { status: 400 });
    }

    // 2. Process Headers & Extract Proxy Flags
    const headers = new Headers();
    let forceCache = false;
    let customTtl = null;

    for (const [key, value] of request.headers.entries()) {
      const lowerKey = key.toLowerCase();

      // Drop Cloudflare proxy headers
      if (lowerKey.startsWith("cf-")) continue;

      // Header-based proxy controls
      if (lowerKey === "x-proxy-force-cache") {
        forceCache = value === "true";
        continue;
      }
      if (lowerKey === "x-proxy-cache-ttl") {
        customTtl = parseInt(value, 10);
        continue;
      }

      headers.set(key, value);
    }

    // 3. Query Param Overrides (Param takes precedence over Header)
    if (requestUrl.searchParams.has("forceCache")) {
      forceCache = requestUrl.searchParams.get("forceCache") === "true";
    }
    if (requestUrl.searchParams.has("ttl")) {
      const parsedTtl = parseInt(requestUrl.searchParams.get("ttl"), 10);
      if (!isNaN(parsedTtl)) customTtl = parsedTtl;
    }

    // 4. Configure Cloudflare Cache Behavior
    const isRangeRequest = headers.has("Range");
    const cfOptions = {
      cacheEverything: true // Ensures Cloudflare honors origin Cache-Control even for HTML
    };

    if (customTtl !== null || forceCache) {
      const ttl = customTtl ?? 31536000;
      cfOptions.cacheTtlByStatus = {
        "200-299": isRangeRequest ? -1 : ttl,
        "404": -1,
        "500-599": -1
      };
    }

    // 5. Execute Request
    const modifiedRequest = new Request(targetUrl, {
      method: request.method,
      headers,
      body: !["GET", "HEAD"].includes(request.method) ? request.body : null,
      redirect: "follow",
      cf: cfOptions
    });

    const response = await fetch(modifiedRequest);

    // 6. Override Response Headers if Custom TTL/Force Cache was active
    if (response.ok && request.method === "GET" && (customTtl !== null || forceCache)) {
      const ttl = customTtl ?? 31536000;
      const newHeaders = new Headers(response.headers);

      if (isRangeRequest || ttl < 1) {
        newHeaders.set("Cache-Control", "no-store");
      } else {
        newHeaders.set("Cache-Control", `public, max-age=${ttl}, immutable`);
      }

      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: newHeaders
      });
    }

    return response;
  }
};
