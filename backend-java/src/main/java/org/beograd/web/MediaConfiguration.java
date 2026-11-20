package org.beograd.web;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.io.Resource;
import org.springframework.web.servlet.config.annotation.ResourceHandlerRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;
import org.springframework.web.servlet.resource.PathResourceResolver;

/** Existing local media, with canonical containment including Windows junctions. */
@Configuration
public class MediaConfiguration implements WebMvcConfigurer {
    private final String directory;
    public MediaConfiguration(@Value("${beograd.media-root}") String directory){this.directory=directory;}
    @Override public void addResourceHandlers(ResourceHandlerRegistry registry) {
        if(directory.isBlank())return;
        Path root=Path.of(directory);
        if(!root.isAbsolute())throw new IllegalArgumentException("BEOGRAD_MEDIA_ROOT must be absolute");
        String uri=root.toUri().toString();
        registry.addResourceHandler("/media/**").addResourceLocations(uri.endsWith("/")?uri:uri+"/")
            .resourceChain(false).addResolver(new PathResourceResolver(){
                @Override protected boolean checkResource(Resource resource,Resource location)throws IOException {
                    try {
                        Path file=resource.getFile().toPath().toRealPath();
                        return Files.isRegularFile(file)&&file.startsWith(root.toRealPath())&&super.checkResource(resource,location);
                    }catch(IOException ex){return false;}
                }
            });
    }
}
