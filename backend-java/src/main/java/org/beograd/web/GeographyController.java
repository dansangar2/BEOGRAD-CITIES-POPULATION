package org.beograd.web;

import jakarta.servlet.http.HttpServletRequest;
import org.beograd.application.Geography;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMethod;
import java.util.Map;

@RestController
public class GeographyController {
    private final Geography geography;
    public GeographyController(Geography geography) { this.geography=geography; }
    @RequestMapping(value="/api/countries/",method={RequestMethod.GET,RequestMethod.HEAD,RequestMethod.POST,RequestMethod.PUT,RequestMethod.PATCH,RequestMethod.DELETE,RequestMethod.OPTIONS})
    public Map<String,Object> countries(HttpServletRequest request) { return geography.countrySummary(language(request),true,"/api/countries/"); }
    @RequestMapping(value="/dashboard/population/",method={RequestMethod.GET,RequestMethod.HEAD,RequestMethod.POST,RequestMethod.PUT,RequestMethod.PATCH,RequestMethod.DELETE,RequestMethod.OPTIONS})
    public Map<String,Object> population(HttpServletRequest request) { return geography.countrySummary(language(request),false,"/dashboard/country/"); }
    @RequestMapping(value={"/api/derived/","/dashboard/derived/"},method={RequestMethod.GET,RequestMethod.HEAD,RequestMethod.POST,RequestMethod.PUT,RequestMethod.PATCH,RequestMethod.DELETE,RequestMethod.OPTIONS})
    public Map<String,Object> derived(HttpServletRequest request) { return geography.derivedSummary(language(request)); }

    @RequestMapping(value="/configs/{slug:[a-zA-Z0-9_-]+}/export-data-csv/",method={RequestMethod.GET,RequestMethod.HEAD,RequestMethod.POST,RequestMethod.PUT,RequestMethod.PATCH,RequestMethod.DELETE,RequestMethod.OPTIONS})
    public ResponseEntity<byte[]> csv(@PathVariable String slug) {
        var csv=geography.configCsv(slug);
        // Match Django's content disposition, UTF-8 BOM and CRLF exactly.
        return ResponseEntity.ok().header("Content-Type","text/csv; charset=utf-8")
            .header("Content-Disposition","attachment; filename=\""+csv.filename().replace("\"", "\\\"")+"\"")
            .body(csv.text().getBytes(java.nio.charset.StandardCharsets.UTF_8));
    }
    static String language(HttpServletRequest request) {
        return RequestLanguage.resolve(request);
    }
}
