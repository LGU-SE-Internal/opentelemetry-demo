package frauddetection

import io.grpc.*
import io.opentelemetry.demo.frauddetection.CheckTransactionRequest
import java.util.*
import java.util.regex.Pattern

class CheckTransactionValidationInterceptor : ServerInterceptor {
    private val transactionIdPattern = Pattern.compile("^[a-zA-Z0-9]{1,64}$")
    private val currencyPattern = Pattern.compile("^[A-Z]{3}$")
    private val validISOCurrencies = setOf(
        "AED", "AFN", "ALL", "AMD", "ANG", "AOA", "ARS", "AUD", "AWG", "AZN", "BAM", "BBD", "BDT", "BGN", "BHD",
        "BIF", "BMD", "BND", "BOB", "BRL", "BSD", "BTN", "BWP", "BYN", "BZD", "CAD", "CDF", "CHF", "CLP", "CNY",
        "COP", "CRC", "CUC", "CUP", "CVE", "CZK", "DJF", "DKK", "DOP", "DZD", "EGP", "ERN", "ETB", "EUR", "FJD",
        "FKP", "GBP", "GEL", "GGP", "GHS", "GIP", "GMD", "GNF", "GTQ", "GYD", "HKD", "HNL", "HRK", "HTG", "HUF",
        "IDR", "ILS", "IMP", "INR", "IQD", "IRR", "ISK", "JEP", "JMD", "JOD", "JPY", "KES", "KGS", "KHR", "KMF",
        "KPW", "KRW", "KWD", "KYD", "KZT", "LAK", "LBP", "LKR", "LRD", "LSL", "LYD", "MAD", "MDL", "MGA", "MKD",
        "MMK", "MNT", "MOP", "MRU", "MUR", "MVR", "MWK", "MXN", "MYR", "MZN", "NAD", "NGN", "NIO", "NOK", "NPR",
        "NZD", "OMR", "PAB", "PEN", "PGK", "PHP", "PKR", "PLN", "PYG", "QAR", "RON", "RSD", "RUB", "RWF", "SAR",
        "SBD", "SCR", "SDG", "SEK", "SGD", "SHP", "SLL", "SOS", "SPL", "SRD", "STN", "SVC", "SYP", "SZL", "THB",
        "TJS", "TMT", "TND", "TOP", "TRY", "TTD", "TVD", "TWD", "TZS", "UAH", "UGX", "USD", "UYU", "UZS", "VEF",
        "VND", "VUV", "WST", "XAF", "XCD", "XDR", "XOF", "XPF", "YER", "ZAR", "ZMW", "ZWD"
    )

    override fun <ReqT : Any, RespT : Any> interceptCall(
        call: ServerCall<ReqT, RespT>,
        headers: Metadata,
        next: ServerCallHandler<ReqT, RespT>
    ): ServerCall.Listener<ReqT> {
        if (call.methodDescriptor.fullMethodName != FraudDetectionServiceGrpc.getCheckTransactionMethod().fullMethodName) {
            return next.startCall(call, headers)
        }

        val requestListener = object : ForwardingServerCallListener.SimpleForwardingServerCallListener<ReqT>(next.startCall(call, headers)) {
            override fun onMessage(message: ReqT) {
                val request = message as CheckTransactionRequest
                val validationError = validateRequest(request)
                if (validationError != null) {
                    val status = Status.INVALID_ARGUMENT.withDescription(validationError)
                    call.close(status, Metadata())
                    return
                }
                super.onMessage(message)
            }
        }

        return requestListener
    }

    private fun validateRequest(request: CheckTransactionRequest): String? {
        // Validate user_id
        if (request.userId.isNullOrEmpty()) {
            return "user_id is required"
        }
        try {
            UUID.fromString(request.userId)
        } catch (e: IllegalArgumentException) {
            return "user_id must be a valid UUID"
        }

        // Validate transaction_id
        if (request.transactionId.isNullOrEmpty()) {
            return "transaction_id is required"
        }
        if (!transactionIdPattern.matcher(request.transactionId).matches()) {
            return "transaction_id must be a 1-64 character alphanumeric string"
        }

        // Validate amount
        if (request.amount <= 0.0) {
            return "amount must be greater than 0"
        }

        // Validate currency
        if (request.currency.isNullOrEmpty() || !currencyPattern.matcher(request.currency).matches() || !validISOCurrencies.contains(request.currency)) {
            return "currency must be a valid 3-letter ISO 4217 code"
        }

        // Validate timestamp
        val currentTime = System.currentTimeMillis()
        if (request.timestamp <= 0 || request.timestamp > currentTime) {
            return "timestamp must be a valid Unix timestamp not in the future"
        }

        return null
    }
}
