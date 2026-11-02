package com.brpportal.imagescan.engine

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import android.graphics.Bitmap
import java.nio.FloatBuffer
import kotlin.math.roundToInt
import kotlin.math.sqrt

/**
 * On-device CLIP image embedder (ViT-B-32, ONNX Runtime).
 * Preprocessing server (open_clip) jaisa hi hai: chhoti side 224 par
 * resize, center crop 224, CLIP mean/std normalize. Output 512-dim
 * L2-normalized vector — server ki embeddings se comparable.
 */
class ClipEmbedder private constructor(private val session: OrtSession) {

    private val env: OrtEnvironment = OrtEnvironment.getEnvironment()

    fun embed(bitmap: Bitmap): FloatArray {
        val input = preprocess(bitmap)
        val tensor = OnnxTensor.createTensor(
            env, FloatBuffer.wrap(input), longArrayOf(1, 3, 224, 224)
        )
        val out = session.run(mapOf(session.inputNames.first() to tensor))
        @Suppress("UNCHECKED_CAST")
        val raw = (out[0].value as Array<FloatArray>)[0]
        out.close()
        tensor.close()
        return normalize(raw)
    }

    /** Ek image ke 5 hisse (poora + 4 kone + center) — chhoti copy gehra match. */
    fun embedTiles(bitmap: Bitmap): List<FloatArray> {
        val w = bitmap.width
        val h = bitmap.height
        val s = minOf(w, h)
        val tiles = mutableListOf<Bitmap>()
        tiles.add(bitmap)
        val boxes = listOf(
            0 to 0, (w - s) to 0, 0 to (h - s), (w - s) to (h - s),
            (w - s) / 2 to (h - s) / 2
        )
        for ((x, y) in boxes) {
            if (s >= 40) tiles.add(Bitmap.createBitmap(bitmap, x, y, s, s))
        }
        return tiles.map { embed(it) }
    }

    private fun preprocess(src: Bitmap): FloatArray {
        val scale = 224f / minOf(src.width, src.height)
        val nw = (src.width * scale).roundToInt().coerceAtLeast(224)
        val nh = (src.height * scale).roundToInt().coerceAtLeast(224)
        val resized = Bitmap.createScaledBitmap(src, nw, nh, true)
        val x0 = (nw - 224) / 2
        val y0 = (nh - 224) / 2
        val crop = Bitmap.createBitmap(resized, x0, y0, 224, 224)
        val pixels = IntArray(224 * 224)
        crop.getPixels(pixels, 0, 224, 0, 0, 224, 224)
        val mean = floatArrayOf(0.48145466f, 0.4578275f, 0.40821073f)
        val std = floatArrayOf(0.26862954f, 0.26130258f, 0.27577711f)
        val data = FloatArray(3 * 224 * 224)
        for (i in pixels.indices) {
            val p = pixels[i]
            data[i] = (((p shr 16) and 0xFF) / 255f - mean[0]) / std[0]
            data[224 * 224 + i] = (((p shr 8) and 0xFF) / 255f - mean[1]) / std[1]
            data[2 * 224 * 224 + i] = ((p and 0xFF) / 255f - mean[2]) / std[2]
        }
        return data
    }

    companion object {
        @Volatile
        private var instance: ClipEmbedder? = null

        fun get(ctx: Context): ClipEmbedder {
            return instance ?: synchronized(this) {
                instance ?: run {
                    check(ModelManager.isReady(ctx)) {
                        "Model download nahi hua — pehle References tab se download karo"
                    }
                    val env = OrtEnvironment.getEnvironment()
                    val opts = OrtSession.SessionOptions().apply {
                        setIntraOpNumThreads(4)
                    }
                    val session = env.createSession(
                        ModelManager.imageModel(ctx).absolutePath, opts
                    )
                    ClipEmbedder(session).also { instance = it }
                }
            }
        }

        fun cosine(a: FloatArray, b: FloatArray): Float {
            var dot = 0f
            for (i in a.indices) dot += a[i] * b[i]
            return dot // dono pehle se L2-normalized hain
        }

        fun normalize(v: FloatArray): FloatArray {
            var n = 0f
            for (x in v) n += x * x
            n = sqrt(n)
            if (n > 0f) for (i in v.indices) v[i] /= n
            return v
        }
    }
}
