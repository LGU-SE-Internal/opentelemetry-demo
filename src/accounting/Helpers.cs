// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using System.Collections;
using Confluent.Kafka;
using Microsoft.Extensions.Diagnostics.HealthChecks;

namespace Accounting
{
    internal static class Helpers
    {
        private static List<string> RelevantPrefixes = ["DOTNET_", "CORECLR_", "OTEL_", "KAFKA_", "POSTGRES_"];

        public static IEnumerable<DictionaryEntry> FilterRelevant(this IDictionary envs)
        {
            foreach (DictionaryEntry env in envs)
            {
                foreach (var prefix in RelevantPrefixes)
                {
                    if (env.Key.ToString()?.StartsWith(prefix, StringComparison.InvariantCultureIgnoreCase) ?? false)
                    {
                        yield return env;
                    }
                }
            }
        }

        public static void OutputInOrder(this IEnumerable<DictionaryEntry> envs)
        {
            foreach (var env in envs.OrderBy(x => x.Key))
            {
                Console.WriteLine(env);
            }
        }

        public static IHealthChecksBuilder AddKafka(this IHealthChecksBuilder builder, string bootstrapServers, string name, IEnumerable<string>? tags = null, TimeSpan? timeout = null)
        {
            return builder.Add(new HealthCheckRegistration(
                name,
                sp => new KafkaHealthCheck(bootstrapServers),
                null,
                tags,
                timeout));
        }

        private class KafkaHealthCheck : IHealthCheck
        {
            private readonly string _bootstrapServers;

            public KafkaHealthCheck(string bootstrapServers)
            {
                _bootstrapServers = bootstrapServers;
            }

            public async Task<HealthCheckResult> CheckHealthAsync(HealthCheckContext context, CancellationToken cancellationToken = default)
            {
                if (string.IsNullOrEmpty(_bootstrapServers))
                {
                    return HealthCheckResult.Unhealthy("Kafka bootstrap servers not configured");
                }

                var config = new AdminClientConfig
                {
                    BootstrapServers = _bootstrapServers,
                    RequestTimeoutMs = 5000
                };

                using var adminClient = new AdminClientBuilder(config).Build();
                try
                {
                    var metadata = adminClient.GetMetadata(TimeSpan.FromSeconds(5));
                    if (metadata.Brokers.Any())
                    {
                        return HealthCheckResult.Healthy("Kafka connection successful");
                    }
                    return HealthCheckResult.Unhealthy("No Kafka brokers found");
                }
                catch (Exception ex)
                {
                    return HealthCheckResult.Unhealthy("Kafka connection failed", ex);
                }
            }
        }
    }
}
