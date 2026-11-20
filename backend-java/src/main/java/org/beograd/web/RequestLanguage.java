package org.beograd.web;

import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletRequest;
import java.util.List;
import java.util.Locale;

/** Public language selection no longer depends on a trusted Django proxy header. */
final class RequestLanguage {
    private static final List<String> SUPPORTED=List.of("es","en","fr","de","ru","it","sr","sr-latn","ar");
    private RequestLanguage() {}
    static String resolve(HttpServletRequest request) {
        if(request.getCookies()!=null)for(Cookie cookie:request.getCookies()) {
            if(cookie.getName().equals("django_language")) {
                String language=supported(cookie.getValue());
                if(language!=null)return language;
            }
        }
        String accept=request.getHeader("Accept-Language");
        if(accept!=null)try {
            for(var range:Locale.LanguageRange.parse(accept)) {
                if(range.getWeight()==0)continue;
                String language=supported(range.getRange());
                if(language!=null)return language;
            }
        }catch(IllegalArgumentException ex){ /* Invalid language headers retain the default. */ }
        return "es";
    }
    private static String supported(String raw) {
        if(raw==null)return null;
        String language=raw.toLowerCase(Locale.ROOT).replace('_','-');
        if(SUPPORTED.contains(language))return language;
        if(language.startsWith("sr-latn-"))return "sr-latn";
        String base=language.split("-",2)[0];
        return SUPPORTED.contains(base)?base:null;
    }
}
