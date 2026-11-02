package com.brpportal.imagescan.engine

import android.content.Context
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL

/**
 * CLIP ONNX models ka manager. Models APK me nahi hote — pehli dafa
 * GitHub Release (models-v1) se download hokar phone me save hote hain,
 * phir hamesha local. Default INT8 (~154 MB); FP32 high-quality optional.
 */
object ModelManager {

    private const val RELEASE =
        "https://github.com/brpportal/visual-image-search/releases/download/models-v1/"

    // variant -> (remote file, local file, approx MB)
    private val FILES = mapOf(
        "int8" to listOf(
            Triple("clip_image_int8.onnx", "clip_image.onnx", 89),
            Triple("clip_text_int8.onnx", "clip_text.onnx", 65)
        ),
        "fp32" to listOf(
            Triple("clip_image.onnx", "clip_image.onnx", 352),
            Triple("clip_text.onnx", "clip_text.onnx", 254)
        )
    )

    fun dir(ctx: Context): File = File(ctx.filesDir, "models").apply { mkdirs() }

    fun variant(ctx: Context): String =
        ctx.getSharedPreferences("cfg", Context.MODE_PRIVATE)
            .getString("model_variant", "int8")!!

    fun setVariant(ctx: Context, v: String) {
        ctx.getSharedPreferences("cfg", Context.MODE_PRIVATE)
            .edit().putString("model_variant", v).apply()
    }

    fun imageModel(ctx: Context): File = File(dir(ctx), "clip_image.onnx")
    fun textModel(ctx: Context): File = File(dir(ctx), "clip_text.onnx")

    /** Dono model files maujood hain aur size theek lagta hai? */
    fun isReady(ctx: Context): Boolean {
        val minBytes = if (variant(ctx) == "fp32") 200_000_000L else 50_000_000L
        return imageModel(ctx).let { it.exists() && it.length() > minBytes } &&
            textModel(ctx).let { it.exists() && it.length() > 10_000_000L }
    }

    fun statusText(ctx: Context): String {
        val v = variant(ctx)
        return if (isReady(ctx)) {
            "✅ Model ready hai (${v.uppercase()}) — on-device engine tayyar hai."
        } else {
            "Model abhi download nahi hua. Pehli dafa download hoga " +
                "(INT8 ~154 MB / FP32 ~606 MB), phir hamesha phone me rahe ga. " +
                "Wi-Fi par download karna."
        }
    }

    /**
     * Blocking download (background thread se call karo).
     * progress: (label, percent or -1) callback.
     */
    fun download(ctx: Context, progress: (String) -> Unit) {
        val v = variant(ctx)
        for ((remote, local, mb) in FILES[v]!!) {
            val dest = File(dir(ctx), local)
            if (dest.exists() && dest.length() > 10_000_000L) {
                progress("✔ $local pehle se hai — skip")
                continue
            }
            progress("⬇ $remote (~$mb MB) download ho raha hai…")
            val part = File(dir(ctx), "$local.part")
            val conn = URL(RELEASE + remote).openConnection() as HttpURLConnection
            conn.connectTimeout = 20000
            conn.readTimeout = 60000
            conn.instanceFollowRedirects = true
            conn.connect()
            check(conn.responseCode in 200..299) { "HTTP ${conn.responseCode}" }
            val total = conn.contentLengthLong
            conn.inputStream.use { input ->
                FileOutputStream(part).use { out ->
                    val buf = ByteArray(1 shl 16)
                    var done = 0L
                    var lastPct = -1
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        done += n
                        if (total > 0) {
                            val pct = (done * 100 / total).toInt()
                            if (pct != lastPct && pct % 5 == 0) {
                                lastPct = pct
                                progress("⬇ $remote — $pct%")
                            }
                        }
                    }
                }
            }
            conn.disconnect()
            check(part.length() > 10_000_000L) { "Download adhoora reh gaya" }
            check(part.renameTo(dest)) { "File save nahi hui" }
            progress("✔ $local ready (${dest.length() / 1_000_000} MB)")
        }
    }
}
