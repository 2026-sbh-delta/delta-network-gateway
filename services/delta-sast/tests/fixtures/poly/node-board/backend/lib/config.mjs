export function commonConfig(env, defaultPort = 8080) {
  const value = env.PORT ?? String(defaultPort);
  if (!/^\d+$/.test(value) || Number(value) < 1 || Number(value) > 65535) {
    throw new Error('PORT must be an integer between 1 and 65535');
  }
  const apiEndpoint = (env.API_ENDPOINT ?? '/api').replace(/\/+$/, '');
  if (!/^\/[a-zA-Z0-9_-]+(?:\/[a-zA-Z0-9_-]+)*$/.test(apiEndpoint)
      || apiEndpoint === '/healthz') {
    throw new Error('API_ENDPOINT must be a path such as /api or /v1/api');
  }
  return {
    host: env.HOST ?? '0.0.0.0',
    port: Number(value),
    apiEndpoint,
    environment: env.APP_ENV ?? 'local',
    version: env.APP_VERSION ?? 'dev',
  };
}
