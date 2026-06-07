use actix_web::App;
use crate::app;

pub async fn create_test_app() -> App {
    // Initialize OTel for tests
    let _ = crate::init_otel();
    app().await
}
