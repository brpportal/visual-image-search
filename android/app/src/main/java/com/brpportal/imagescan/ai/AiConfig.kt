package com.brpportal.imagescan.ai

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * User ki apni AI setting: custom Base URL + API Key + Model.
 * Koi bhi OpenAI-compatible platform chalega (OpenRouter, Apmix,
 * OpenAI, waghera). Key encrypted save hoti hai; AI optional hai —
 * core system bagher AI ke bhi mukammal chalta hai.
 */
class AiConfig(private val ctx: Context) {

    private val prefs: SharedPreferences by lazy {
        try {
            val masterKey = MasterKey.Builder(ctx)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                .build()
            EncryptedSharedPreferences.create(
                ctx, "ai_secure", masterKey,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
            )
        } catch (e: Exception) {
            ctx.getSharedPreferences("ai_plain", Context.MODE_PRIVATE)
        }
    }

    var baseUrl: String
        get() = prefs.getString("base_url", "")!!
        set(v) = prefs.edit().putString("base_url", v.trim().trimEnd('/')).apply()

    var apiKey: String
        get() = prefs.getString("api_key", "")!!
        set(v) = prefs.edit().putString("api_key", sanitizeKey(v)).apply()

    var model: String
        get() = prefs.getString("model", "")!!
        set(v) = prefs.edit().putString("model", v.trim()).apply()

    val isSet: Boolean
        get() = baseUrl.isNotEmpty() && apiKey.isNotEmpty() && model.isNotEmpty()

    companion object {
        /**
         * Paste ki aam ghaltiyon se safai: spaces, newlines aur
         * ghair-ASCII aksar (masalan ghalti se chipka hua Arabic
         * harf) hata do — warna Authorization header fail hota hai.
         */
        fun sanitizeKey(raw: String): String =
            raw.filter { it.code in 0x21..0x7E }

        val PRESETS = listOf(
            Triple("OpenRouter", "https://openrouter.ai/api/v1", ""),
            Triple("Apmix", "https://api.apmix.ai/v1", ""),
            Triple("OpenAI", "https://api.openai.com/v1", ""),
            Triple("Custom", "", "")
        )
    }
}
