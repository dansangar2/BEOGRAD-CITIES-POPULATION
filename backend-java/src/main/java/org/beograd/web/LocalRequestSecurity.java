package org.beograd.web;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Map;
import java.util.Set;
import org.beograd.infrastructure.TextCatalog;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/** Loopback host validation and the existing Django double-submit CSRF protocol. */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE+10)
public class LocalRequestSecurity extends OncePerRequestFilter {
    private static final String CHARS="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
    private static final Set<String> SAFE=Set.of("GET","HEAD","OPTIONS","TRACE");
    private final TextCatalog texts;
    private final ObjectMapper json;
    public LocalRequestSecurity(TextCatalog texts,ObjectMapper json){this.texts=texts;this.json=json;}
    @Override protected void doFilterInternal(HttpServletRequest req,HttpServletResponse res,FilterChain chain) throws ServletException,IOException {
        String host=req.getHeader("Host");
        if(host==null||!host.toLowerCase(java.util.Locale.ROOT).matches("(?:localhost|127\\.0\\.0\\.1)\\.?(?::[0-9]{1,5})?")) {
            reject(req,res,400,"invalid_host");return;
        }
        // This operation has its own per-launch secret, checked by the controller.
        if(!SAFE.contains(req.getMethod())&&!req.getRequestURI().equals("/internal/shutdown")) {
            String origin=req.getHeader("Origin");
            String ownOrigin=req.getScheme()+"://"+host;
            if(origin!=null&&!origin.equals(ownOrigin)){reject(req,res,403,"csrf_failed");return;}
            String cookie=null;
            if(req.getCookies()!=null)for(Cookie value:req.getCookies())if(value.getName().equals("csrftoken"))cookie=secret(value.getValue());
            String token=secret(req.getHeader("X-CSRFToken"));
            if(cookie==null||token==null||!MessageDigest.isEqual(cookie.getBytes(StandardCharsets.US_ASCII),token.getBytes(StandardCharsets.US_ASCII))) {
                reject(req,res,403,"csrf_failed");return;
            }
        }
        res.setHeader("X-Content-Type-Options","nosniff");
        res.setHeader("X-Frame-Options","DENY");
        res.addHeader("Vary","Accept-Language, Cookie");
        chain.doFilter(req,res);
    }
    private void reject(HttpServletRequest req,HttpServletResponse res,int status,String code)throws IOException {
        res.setStatus(status);res.setContentType("application/json");res.setCharacterEncoding("UTF-8");
        json.writeValue(res.getOutputStream(),Map.of("code",code,"error",texts.translate("No se pudieron cargar los datos.",RequestLanguage.resolve(req))));
    }
    private static String secret(String token) {
        if(token==null||(token.length()!=32&&token.length()!=64)||!token.chars().allMatch(c->CHARS.indexOf(c)>=0))return null;
        if(token.length()==32)return token;
        var value=new StringBuilder(32);
        for(int i=0;i<32;i++)value.append(CHARS.charAt((CHARS.indexOf(token.charAt(32+i))-CHARS.indexOf(token.charAt(i))+62)%62));
        return value.toString();
    }
}
