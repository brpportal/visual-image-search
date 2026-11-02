package com.brpportal.imagescan.engine

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.UUID

/**
 * User ki reference images — jin ko system internet par dhundhe ga.
 * Har reference: compressed copy (filesDir/references/<id>.jpg) +
 * us ki CLIP embedding (<id>.vec, 512 x float32 LE). Meta refs.json me.
 */
class ReferencesManager(private val ctx: Context) {

    data class Ref(val id: String, val name: String, val file: File, val hasVector: Boolean)

    private val dir: File = File(ctx.filesDir, "references").apply { mkdirs() }
    private val metaFile: File = File(dir, "refs.json")

    private fun loadMeta(): JSONArray {
        if (!metaFile.exists()) return JSONArray()
        return try {
            JSONArray(metaFile.readText())
        } catch (e: Exception) {
            JSONArray()
        }
    }

    private fun saveMeta(arr: JSONArray) {
        metaFile.writeText(arr.toString())
    }

    fun list(): List<Ref> {
        val arr = loadMeta()
        val out = mutableListOf<Ref>()
        for (i in 0 until arr.length()) {
            val o = arr.getJSONObject(i)
            val id = o.getString("id")
            out.add(
                Ref(
                    id = id,
                    name = o.optString("name", id),
                    file = File(dir, "$id.jpg"),
                    hasVector = File(dir, "$id.vec").exists()
                )
            )
        }
        return out
    }

    fun count(): Int = loadMeta().length()

    /**
     * Nayi reference add karo: image save + embedding bana kar store.
     * Background thread se call karo (embedding me waqt lagta hai).
     */
    fun add(bitmap: Bitmap, name: String): Ref {
        val id = UUID.randomUUID().toString().take(8)
        // bari images ko 1024 max side par rakho — matching ke liye kafi
        val scaled = scaleMax(bitmap, 1024)
        val imgFile = File(dir, "$id.jpg")
        imgFile.outputStream().use { out ->
            scaled.compress(Bitmap.CompressFormat.JPEG, 88, out)
        }
        val vec = ClipEmbedder.get(ctx).embed(scaled)
        writeVector(File(dir, "$id.vec"), vec)
        val arr = loadMeta()
        arr.put(JSONObject().put("id", id).put("name", name))
        saveMeta(arr)
        return Ref(id, name, imgFile, true)
    }

    fun delete(id: String) {
        File(dir, "$id.jpg").delete()
        File(dir, "$id.vec").delete()
        val arr = loadMeta()
        val keep = JSONArray()
        for (i in 0 until arr.length()) {
            val o = arr.getJSONObject(i)
            if (o.getString("id") != id) keep.put(o)
        }
        saveMeta(keep)
    }

    fun vectorOf(id: String): FloatArray? {
        val f = File(dir, "$id.vec")
        if (!f.exists()) return null
        val buf = ByteBuffer.wrap(f.readBytes()).order(ByteOrder.LITTLE_ENDIAN)
        val out = FloatArray(512)
        buf.asFloatBuffer().get(out)
        return out
    }

    /** Tamam references apni vectors ke saath (hunt/scan ke liye). */
    fun allWithVectors(): List<Pair<Ref, FloatArray>> =
        list().mapNotNull { r -> vectorOf(r.id)?.let { v -> r to v } }

    private fun writeVector(f: File, v: FloatArray) {
        val buf = ByteBuffer.allocate(v.size * 4).order(ByteOrder.LITTLE_ENDIAN)
        buf.asFloatBuffer().put(v)
        f.writeBytes(buf.array())
    }

    private fun scaleMax(b: Bitmap, maxSide: Int): Bitmap {
        val m = maxOf(b.width, b.height)
        if (m <= maxSide) return b
        val s = maxSide.toFloat() / m
        return Bitmap.createScaledBitmap(
            b, (b.width * s).toInt().coerceAtLeast(1),
            (b.height * s).toInt().coerceAtLeast(1), true
        )
    }

    companion object {
        fun decodeFile(f: File): Bitmap? = BitmapFactory.decodeFile(f.absolutePath)
    }
}
