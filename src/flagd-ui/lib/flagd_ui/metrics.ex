defmodule FlagdUI.Metrics do
  use Prometheus.Metric

  def setup do
    Counter.declare(
      name: :flagd_ui_rate_limited_requests_total,
      help: "Total number of requests that were rate limited",
      labels: [:ip_address, :endpoint_type, :status]
    )
  end
end
