package metrics

import (
	"strconv"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

const namespace = "kafka_collector"

var (
	messagesConsumed = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Namespace: namespace,
			Name:      "messages_consumed_total",
			Help:      "Total number of messages successfully fetched from Kafka brokers",
		},
		[]string{"topic", "partition"},
	)

	messagesProcessedSuccess = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Namespace: namespace,
			Name:      "messages_processed_success_total",
			Help:      "Total number of messages processed without errors",
		},
		[]string{"topic", "partition"},
	)

	messagesProcessedFailure = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Namespace: namespace,
			Name:      "messages_processed_failure_total",
			Help:      "Total number of messages that failed processing",
		},
		[]string{"topic", "partition", "error_type"},
	)

	consumerLagCurrent = promauto.NewGaugeVec(
		prometheus.GaugeOpts{
			Namespace: namespace,
			Name:      "consumer_lag_current",
			Help:      "Current number of messages remaining to be processed for the consumer group on a given topic partition",
		},
		[]string{"topic", "partition"},
	)

	messageProcessingDuration = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Namespace: namespace,
			Name:      "message_processing_duration_seconds",
			Help:      "Time taken to process a single message from consumption to completion",
			Buckets:   []float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10},
		},
		[]string{"topic", "partition", "status"},
	)
)

func IncMessagesConsumed(topic string, partition int32) {
	messagesConsumed.WithLabelValues(topic, strconv.Itoa(int(partition))).Inc()
}

func IncMessagesProcessedSuccess(topic string, partition int32) {
	messagesProcessedSuccess.WithLabelValues(topic, strconv.Itoa(int(partition))).Inc()
}

func IncMessagesProcessedFailure(topic string, partition int32, errorType string) {
	messagesProcessedFailure.WithLabelValues(topic, strconv.Itoa(int(partition)), errorType).Inc()
}

func SetConsumerLag(topic string, partition int32, lag int64) {
	consumerLagCurrent.WithLabelValues(topic, strconv.Itoa(int(partition))).Set(float64(lag))
}

func ObserveProcessingDuration(topic string, partition int32, status string, duration time.Duration) {
	messageProcessingDuration.WithLabelValues(topic, strconv.Itoa(int(partition)), status).Observe(duration.Seconds())
}
