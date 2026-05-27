# Keep JSBridge methods — ProGuard would rename/remove them and break
# the JavaScript bridge since JS calls methods by exact name
-keep class com.rasova.pos.JSBridge { *; }
-keepclassmembers class com.rasova.pos.JSBridge {
    @android.webkit.JavascriptInterface <methods>;
}

# Keep OkHttp internals
-dontwarn okhttp3.**
-keep class okhttp3.** { *; }
