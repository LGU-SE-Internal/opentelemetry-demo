defmodule FlagdUI.PromEx do
  use PromEx, otp_app: :flagd_ui

  alias PromEx.Plugins

  @impl true
  def plugins do
    [
      # Phoenix metrics
      Plugins.Phoenix,
      # BEAM VM metrics
      Plugins.Beam,
      # Custom business metrics
      {Plugins.Custom, metrics: &custom_metrics/1}
    ]
  end

  @impl true
  def dashboard_assigns do
    [
      datasource_id: "prometheus"
    ]
  end

  @impl true
  def dashboards do
    []
  end

  defp custom_metrics(_opts) do
    [
      # Flag evaluation counter
      PromEx.MetricTypes.Counter.build(
        event_name: [:flagd_ui, :flag, :evaluation],
        metric_name: :flagd_ui_flag_evaluations_total,
        description: "Total count of flag evaluation operations",
        tags: [:flag_key, :result]
      ),
      # Flag modification counter
      PromEx.MetricTypes.Counter.build(
        event_name: [:flagd_ui, :flag, :modification],
        metric_name: :flagd_ui_flag_modifications_total,
        description: "Total count of flag modification operations",
        tags: [:flag_key, :operation, :result]
      ),
      # API request duration histogram
      PromEx.MetricTypes.Histogram.build(
        event_name: [:flagd_ui, :api, :request, :stop],
        metric_name: :flagd_ui_api_request_duration_seconds,
        description: "Latency distribution of flag management API requests",
        tags: [:endpoint],
        unit: {:second, :native}
      ),
      # Flagd connection health gauge
      PromEx.MetricTypes.Gauge.build(
        event_name: [:flagd_ui, :connection, :health],
        metric_name: :flagd_connection_health_status,
        description: "Flagd connection health: 1 = healthy, 0 = unhealthy",
        tags: [:connection_id]
      )
    ]
  end
end
