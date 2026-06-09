package frauddetection.health

import org.eclipse.microprofile.health.HealthCheck
import org.eclipse.microprofile.health.HealthCheckResponse
import org.eclipse.microprofile.health.Readiness
import jakarta.enterprise.context.ApplicationScoped
import org.apache.kafka.clients.producer.KafkaProducer
import org.apache.kafka.clients.producer.ProducerConfig
import org.apache.kafka.common.serialization.StringSerializer
import java.sql.DriverManager
import java.util.*

@Readiness
@ApplicationScoped
class ServiceReadinessCheck : HealthCheck {

    override fun call(): HealthCheckResponse {
        val responseBuilder = HealthCheckResponse.named("service-readiness")

        // Check Kafka connectivity
        val kafkaStatus = checkKafkaConnection()
        responseBuilder.withData("kafka", kafkaStatus)

        // Check PostgreSQL connectivity
        val postgresStatus = checkPostgresConnection()
        responseBuilder.withData("postgresql", postgresStatus)

        return if (kafkaStatus == "UP" && postgresStatus == "UP") {
            responseBuilder.up().build()
        } else {
            responseBuilder.down().build()
        }
    }

    private fun checkKafkaConnection(): String {
        val props = Properties()
        props[ProducerConfig.BOOTSTRAP_SERVERS_CONFIG] = System.getenv("KAFKA_BROKER") ?: "kafka:9092"
        props[ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG] = StringSerializer::class.java.name
        props[ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG] = StringSerializer::class.java.name
        props[ProducerConfig.REQUEST_TIMEOUT_MS_CONFIG] = 3000
        props[ProducerConfig.DELIVERY_TIMEOUT_MS_CONFIG] = 3000

        return try {
            KafkaProducer<String, String>(props).use { producer ->
                producer.partitionsFor("fraud-events")
                "UP"
            }
        } catch (e: Exception) {
            "DOWN: ${e.message}"
        }
    }

    private fun checkPostgresConnection(): String {
        val dbUrl = System.getenv("DB_URL") ?: "jdbc:postgresql://postgres:5432/fraud"
        val dbUser = System.getenv("DB_USER") ?: "postgres"
        val dbPassword = System.getenv("DB_PASSWORD") ?: "postgres"

        return try {
            DriverManager.getConnection(dbUrl, dbUser, dbPassword).use { conn ->
                if (conn.isValid(2)) {
                    "UP"
                } else {
                    "DOWN: Connection invalid"
                }
            }
        } catch (e: Exception) {
            "DOWN: ${e.message}"
        }
    }
}
