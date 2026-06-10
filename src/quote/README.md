# Quote Service

The Quote Service calculates the shipping costs,
based on the number of items to be shipped.

It is a PHP based service, using a combination of automatic and manual instrumentation.

## Docker Build

To build the quote service, run the following from root directory
of opentelemetry-demo

```sh
docker compose build quote
```

## Run the service

Execute the below command to run the service.

```sh
docker compose up quote
```

In order to get traffic into the service you have to deploy
the whole opentelemetry-demo.

Please follow the root README to do so.

## Configuration

The quote service supports the following environment variables for dynamic shipping cost configuration:

| Variable Name | Type | Default Value | Description |
| --- | --- | --- | --- |
| `SHIPPING_BASE_COST_PER_ITEM` | float | 8.99 | Base shipping cost added per item in the order. Must be a non-negative number. |
| `SHIPPING_WEIGHT_SURCHARGE_PER_KG` | float | 0.0 | Additional surcharge applied per kilogram of total order weight. Must be a non-negative number. |
| `SHIPPING_MINIMUM_ORDER_COST` | float | 8.99 | Minimum shipping cost charged if calculated total is lower than this value. Must be a non-negative number. |

All configuration values are validated during service startup. Invalid values (non-numeric or negative) will cause the service to fail to start with an explicit error message.

## Development

To build and run the quote service locally:

```sh
docker build src/quote --target base -t quote
cd src/quote
docker run --rm -it -v $(pwd):/var/www -e QUOTE_PORT=8999 -p "8999:8999" quote
```

Then, send some curl requests:

```sh
curl --location 'http://localhost:8999/getquote' \
--header 'Content-Type: application/json' \
--data '{"numberOfItems":3}'
```
