defmodule FlagdUiWeb.TestController do
  use FlagdUiWeb, :controller

  def long_running(conn, %{"duration" => duration}) do
    {duration, _} = Integer.parse(duration)
    Process.sleep(duration * 1000)
    json(conn, %{status: "ok"})
  end
end
