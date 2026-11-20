package org.beograd.infrastructure;

import java.nio.file.Files;
import java.nio.file.Path;
import java.io.IOException;
import java.util.Locale;

final class LocalMedia {
    private LocalMedia(){}
    static String url(String relative) {
        String media=System.getenv("BEOGRAD_MEDIA_ROOT");
        if(media==null||media.isBlank()||relative.isBlank())return "";
        // Explicit containment, including links, before reading any file.
        try {
            Path root=Path.of(media).toRealPath();
            var parts=new java.util.ArrayList<String>();
            for(String part:relative.strip().replace('\\','/').split("/"))if(!part.isEmpty()&&!part.equals(".")&&!part.equals(".."))parts.add(part);
            Path file=root.resolve(String.join("/",parts)).toRealPath();
            if(!file.startsWith(root)||!Files.isRegularFile(file)||Files.size(file)==0)return "";
            byte[] head;
            try(var stream=Files.newInputStream(file)){head=stream.readNBytes(4096);}
            String lower=new String(head,java.nio.charset.StandardCharsets.UTF_8).replaceFirst("^\uFEFF", "").stripLeading().toLowerCase(Locale.ROOT);
            String name=file.getFileName().toString().toLowerCase(Locale.ROOT);
            boolean valid=true;
            if(name.endsWith(".svg"))valid=lower.startsWith("<?xml")||lower.substring(0,Math.min(2048,lower.length())).contains("<svg");
            else if(name.endsWith(".png"))valid=head.length>=8&&java.util.Arrays.equals(java.util.Arrays.copyOf(head,8),new byte[]{(byte)137,80,78,71,13,10,26,10});
            else if(name.endsWith(".jpg")||name.endsWith(".jpeg"))valid=head.length>=2&&(head[0]&255)==255&&(head[1]&255)==216;
            else if(name.endsWith(".gif"))valid=lower.startsWith("gif87a")||lower.startsWith("gif89a");
            else if(name.endsWith(".webp"))valid=lower.startsWith("riff")&&lower.length()>=12&&lower.substring(8,12).equals("webp");
            return valid?"/media/"+StoredAssets.quote(String.join("/",parts),"/._-()"):"";
        }catch(IOException|IllegalArgumentException ex){return "";}
    }
}
