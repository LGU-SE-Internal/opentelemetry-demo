# Load Generator

The load generator creates simulated traffic to the demo.

## Accessing the Load Generator

You can access the web interface to Locust at `http://localhost:8080/loadgen/`.

## Modifying the Load Generator

Please see the [Locust
documentation](https://docs.locust.io/en/2.16.0/writing-a-locustfile.html) to
learn more about modifying the locustfile.

## people.json File

The `people.json` file contains sample user data that the load generator uses when simulating checkout processes. Each entry in this file represents a user persona with personal, address, and payment information used to complete mock purchases.

### Required Fields
| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `email` | string | User's email address | `"larry_sergei@example.com"` |
| `name` | string | User's full name | `"Larry Sergei"` |
| `id` | string | Unique identifier for the user | `"usr_123456"` |
| `address` | object | User's physical address | See address fields below |
| `userCurrency` | string | Currency the user prefers to use | `"USD"` |
| `creditCard` | object | User's payment card information | See credit card fields below |

#### Address Object Fields
| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `streetAddress` | string | Street address including number | `"1600 Amphitheatre Parkway"` |
| `zipCode` | string | Postal/zip code | `"94043"` |
| `city` | string | City name | `"Mountain View"` |
| `state` | string | State/region code | `"CA"` |
| `country` | string | Country name | `"United States"` |

#### Credit Card Object Fields
| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `creditCardNumber` | string | Payment card number (formatted with hyphens) | `"4432-8015-6152-0454"` |
| `creditCardExpirationMonth` | integer | 1-indexed expiration month | `1` |
| `creditCardExpirationYear` | integer | 4-digit expiration year | `2039` |
| `creditCardCvv` | integer | Card verification value | `672` |

### Optional Fields
There are currently no optional fields for people.json entries. All fields documented above are required.

### Important Notes
- The file must contain a valid JSON array of user entries
- All entries must follow the schema documented above for the load generator to work correctly
- Any invalid entries or JSON formatting errors will cause the load generator to fail on startup
