package com.brpportal.imagescan.ai

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * OpenAI-compatible chat client — deep research ke liye:
 * scan/hunt results ka analysis, summary, behtar queries.
 * Tamam calls background thread se.
 */
class AiClient(ctx: Context) {

    private val cfg = AiConfig(ctx)

    private fun request(method: String, path: String, body: JSONObject?): JSONObject {
        check(cfg.baseUrl.isNotEmpty() && cfg.apiKey.isNotEmpty()) {
            "Pehle AI tab me Base URL aur API Key save karo"
        }
        val conn = URL(cfg.baseUrl + path).openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.connectTimeout = 20000
        conn.readTimeout = 90000
        conn.setRequestProperty("Authorization", "Bearer ${cfg.apiKey}")
        conn.setRequestProperty("Content-Type", "application/json")
        if (body != null) {
            conn.doOutput = true
            conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
        }
        val code = conn.responseCode
        val text = try {
            if (code in 200..299) conn.inputStream.bufferedReader().readText()
            else conn.errorStream?.bufferedReader()?.readText() ?: ""
        } catch (e: Exception) {
            ""
        }
        conn.disconnect()
        if (code !in 200..299) {
            val msg = try {
                JSONObject(text).optJSONObject("error")?.optString("message")
            } catch (e: Exception) {
                null
            }
            throw RuntimeException("AI HTTP $code${if (!msg.isNullOrEmpty()) ": $msg" else ""}")
        }
        return JSONObject(text)
    }

    /** Ek sawal ka jawab (deep research / analysis ke liye). */
    fun chat(prompt: String, system: String = DEFAULT_SYSTEM): String {
        check(cfg.model.isNotEmpty()) { "Pehle AI tab me model ka naam dalo" }
        val messages = JSONArray()
            .put(JSONObject().put("role", "system").put("content", system))
            .put(JSONObject().put("role", "user").put("content", prompt))
        val body = JSONObject()
            .put("model", cfg.model)
            .put("messages", messages)
        val resp = request("POST", "/chat/completions", body)
        return resp.getJSONArray("choices").getJSONObject(0)
            .getJSONObject("message").getString("content").trim()
    }

    /** Connection test — chhota sa jawab. */
    fun test(): String = chat("Sirf ek line me jawab do: AI connection theek hai?")

    /** Platform ke available models (agar endpoint deta ho). */
    fun fetchModels(): List<String> {
        val resp = request("GET", "/models", null)
        val arr = resp.getJSONArray("data")
        val out = mutableListOf<String>()
        for (i in 0 until arr.length()) out.add(arr.getJSONObject(i).getString("id"))
        return out.sorted()
    }

    companion object {
        const val DEFAULT_SYSTEM =
            "Tum ek image-search research assistant ho. Roman Hinglish me " +
                "mukhtasar aur saaf jawab do. Sirf di gayi maloomat par " +
                "baat karo, andaza mat lagao."
    }
}
