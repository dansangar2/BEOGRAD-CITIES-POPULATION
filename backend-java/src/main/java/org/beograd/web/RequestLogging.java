package org.beograd.web;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class RequestLogging extends OncePerRequestFilter {
    private static final Logger LOG=LoggerFactory.getLogger(RequestLogging.class);
    @Override protected void doFilterInternal(HttpServletRequest req,HttpServletResponse res,FilterChain chain) throws ServletException,IOException {
        String id=UUID.randomUUID().toString();
        res.setHeader("X-Request-ID",id);
        long start=System.nanoTime();
        try { chain.doFilter(req,res); }
        finally { LOG.info("request_id={} method={} status={} duration_ms={}",id,req.getMethod(),res.getStatus(),(System.nanoTime()-start)/1_000_000); }
    }
}
