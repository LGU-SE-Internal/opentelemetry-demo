package io.opentelemetry.demo.frauddetection;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.HandlerInterceptor;

@Component
public class RequestTrackingInterceptor implements HandlerInterceptor {
    @Autowired
    private DefaultShutdownHandler shutdownHandler;

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) throws Exception {
        if (shutdownHandler.isShuttingDown()) {
            response.setStatus(HttpServletResponse.SC_SERVICE_UNAVAILABLE);
            return false;
        }
        shutdownHandler.incrementActiveRequests();
        return true;
    }

    @Override
    public void afterCompletion(HttpServletRequest request, HttpServletResponse response, Object handler, Exception ex) throws Exception {
        shutdownHandler.decrementActiveRequests();
    }
}
