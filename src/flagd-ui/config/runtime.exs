# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

import Config

# config/runtime.exs is executed for all environments, including
# during releases. It is executed after compilation and before the
# system starts, so it is typically used to load production configuration
# and secrets from environment variables or elsewhere. Do not define
# any compile-time configuration in here, as it won't be applied.
# The block below contains prod specific runtime configuration.

# ## Using releases
#
# If you use `mix release`, you need to explicitly enable the server
# by passing the PHX_SERVER=true when you start it:
#
#     PHX_SERVER=true bin/flagd_ui start
#
# Alternatively, you can use `mix phx.gen.release` to generate a `bin/server`
# script that automatically sets the env var above.
if System.get_env("PHX_SERVER") do
  config :flagd_ui, FlagdUiWeb.Endpoint, server: true
end

if config_env() == :prod do
  # The secret key base is used to sign/encrypt cookies and other secrets.
  # A default value is used in config/dev.exs and config/test.exs but you
  # want to use a different value for prod and you most likely don't want
  # to check this value into version control, so we use an environment
  # variable instead.
  secret_key_base =
    System.get_env("SECRET_KEY_BASE") ||
      raise """
      environment variable SECRET_KEY_BASE is missing.
      You can generate one by calling: mix phx.gen.secret
      """

  otel_endpoint =
    System.get_env("OTEL_EXPORTER_OTLP_ENDPOINT") ||
      raise """
      environment variable OTEL_EXPORTER_OTLP_ENDPOINT is missing.
      """

  host = System.get_env("PHX_HOST") || "example.com"
  port = String.to_integer(System.get_env("FLAGD_UI_PORT") || "4000")

  # TLS Configuration
  tls_enabled = System.get_env("FLAGD_UI_TLS_ENABLED") == "true"
  tls_cert_path = System.get_env("FLAGD_UI_TLS_CERT_PATH") || ""
  tls_key_path = System.get_env("FLAGD_UI_TLS_KEY_PATH") || ""
  tls_client_ca_path = System.get_env("FLAGD_UI_TLS_CLIENT_CA_PATH") || ""
  tls_client_require = if System.get_env("FLAGD_UI_TLS_CLIENT_REQUIRE") do
    System.get_env("FLAGD_UI_TLS_CLIENT_REQUIRE") == "true"
  else
    tls_client_ca_path != ""
  end

  if tls_enabled do
    if tls_cert_path == "" or tls_key_path == "" do
      raise "Missing required TLS configuration: FLAGD_UI_TLS_CERT_PATH and FLAGD_UI_TLS_KEY_PATH must be set when TLS is enabled"
    end

    # Validate certificate and key files exist and are readable
    unless File.exists?(tls_cert_path) and File.readable?(tls_cert_path) do
      raise "Invalid TLS certificate file: #{tls_cert_path} does not exist or is not readable"
    end

    unless File.exists?(tls_key_path) and File.readable?(tls_key_path) do
      raise "Invalid TLS private key file: #{tls_key_path} does not exist or is not readable"
    end

    # Base HTTPS config
    https_opts = [
      ip: {0, 0, 0, 0, 0, 0, 0, 0},
      port: port,
      cipher_suite: :strong,
      keyfile: tls_key_path,
      certfile: tls_cert_path
    ]

    # Add mTLS config if client CA is provided
    https_opts = if tls_client_ca_path != "" do
      unless File.exists?(tls_client_ca_path) and File.readable?(tls_client_ca_path) do
        raise "Invalid client CA certificate file: #{tls_client_ca_path} does not exist or is not readable"
      end

      https_opts ++ [
        cacertfile: tls_client_ca_path,
        verify: if tls_client_require, do: :verify_peer, else: :verify_none,
        fail_if_no_peer_cert: tls_client_require
      ]
    else
      https_opts
    end
  end

  config :flagd_ui, :dns_cluster_query, System.get_env("DNS_CLUSTER_QUERY")

  endpoint_config = [
    url: [host: host, port: if tls_enabled, do: 443, else: port, scheme: if tls_enabled, do: "https", else: "http"],
    check_origin: false,
    secret_key_base: secret_key_base
  ]

  endpoint_config = if tls_enabled do
    Keyword.put(endpoint_config, :https, https_opts)
  else
    Keyword.put(endpoint_config, :http, [
      ip: {0, 0, 0, 0, 0, 0, 0, 0},
      port: port
    ])
  end

  config :flagd_ui, FlagdUiWeb.Endpoint, endpoint_config

  config :opentelemetry, :processors,
    otel_batch_processor: %{
      exporter: {:opentelemetry_exporter, %{endpoints: [otel_endpoint]}}
    }

  # ## SSL Support
  #
  # To get SSL working, you will need to add the `https` key
  # to your endpoint configuration:
  #
  #     config :flagd_ui, FlagdUiWeb.Endpoint,
  #       https: [
  #         ...,
  #         port: 443,
  #         cipher_suite: :strong,
  #         keyfile: System.get_env("SOME_APP_SSL_KEY_PATH"),
  #         certfile: System.get_env("SOME_APP_SSL_CERT_PATH")
  #       ]
  #
  # The `cipher_suite` is set to `:strong` to support only the
  # latest and more secure SSL ciphers. This means old browsers
  # and clients may not be supported. You can set it to
  # `:compatible` for wider support.
  #
  # `:keyfile` and `:certfile` expect an absolute path to the key
  # and cert in disk or a relative path inside priv, for example
  # "priv/ssl/server.key". For all supported SSL configuration
  # options, see https://hexdocs.pm/plug/Plug.SSL.html#configure/1
  #
  # We also recommend setting `force_ssl` in your config/prod.exs,
  # ensuring no data is ever sent via http, always redirecting to https:
  #
  #     config :flagd_ui, FlagdUiWeb.Endpoint,
  #       force_ssl: [hsts: true]
  #
  # Check `Plug.SSL` for all available options in `force_ssl`.

  # ## Configuring the mailer
  #
  # In production you need to configure the mailer to use a different adapter.
  # Also, you may need to configure the Swoosh API client of your choice if you
  # are not using SMTP. Here is an example of the configuration:
  #
  #     config :flagd_ui, FlagdUi.Mailer,
  #       adapter: Swoosh.Adapters.Mailgun,
  #       api_key: System.get_env("MAILGUN_API_KEY"),
  #       domain: System.get_env("MAILGUN_DOMAIN")
  #
  # For this example you need include a HTTP client required by Swoosh API client.
  # Swoosh supports Hackney, Req and Finch out of the box:
  #
  #     config :swoosh, :api_client, Swoosh.ApiClient.Hackney
  #
  # See https://hexdocs.pm/swoosh/Swoosh.html#module-installation for details.
end
