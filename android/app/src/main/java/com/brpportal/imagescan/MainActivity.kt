package com.brpportal.imagescan

import android.annotation.SuppressLint
import android.app.Activity
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/**
 * Image Scan — frontend app. Backend (Python + CLIP) local environment
 * me chalta hai: phone par Termux ke andar, ya kisi remote server par.
 * App khulne par backend check hota hai; na mile to Setup screen
 * backend environment install karne ka kehti hai.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var web: WebView
    private lateinit var setupView: View
    private lateinit var settingsView: View
    private lateinit var setupStatus: TextView
    private lateinit var editBase: EditText
    private lateinit var logsView: TextView

    private var fileCallback: ValueCallback<Array<Uri>>? = null

    private fun base(): String {
        val prefs = getSharedPreferences("cfg", Context.MODE_PRIVATE)
        return prefs.getString("base", "http://127.0.0.1:8000")!!
            .trim().trimEnd('/')
    }

    private fun saveBase(v: String) {
        getSharedPreferences("cfg", Context.MODE_PRIVATE)
            .edit().putString("base", v.trim().trimEnd('/')).apply()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        web = findViewById(R.id.web)
        setupView = findViewById(R.id.setupView)
        settingsView = findViewById(R.id.settingsView)
        setupStatus = findViewById(R.id.setupStatus)
        editBase = findViewById(R.id.editBase)
        logsView = findViewById(R.id.logsView)
        editBase.setText(base())

        setupWebView()

        findViewById<Button>(R.id.navScan).setOnClickListener { show("scan") }
        findViewById<Button>(R.id.navSetup).setOnClickListener { show("setup") }
        findViewById<Button>(R.id.navSettings).setOnClickListener { show("settings") }

        findViewById<Button>(R.id.btnInstallTermux).setOnClickListener {
            startActivity(Intent(Intent.ACTION_VIEW,
                Uri.parse("https://f-droid.org/packages/com.termux/")))
        }
        findViewById<Button>(R.id.btnCopyCmd).setOnClickListener {
            val cmd = "pkg update -y && pkg install -y python git && " +
                "git clone https://github.com/brpportal/visual-image-search ~/vis && " +
                "cd ~/vis && bash termux-setup.sh"
            val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            cm.setPrimaryClip(ClipData.newPlainText("termux-setup", cmd))
            Toast.makeText(this, "Command copy ho gaya — Termux me paste karo",
                Toast.LENGTH_LONG).show()
        }
        findViewById<Button>(R.id.btnStartBackend).setOnClickListener { startBackend() }
        findViewById<Button>(R.id.btnCheckBackend).setOnClickListener { checkBackend(true) }
        findViewById<Button>(R.id.btnSaveBase).setOnClickListener {
            saveBase(editBase.text.toString())
            Toast.makeText(this, "Backend URL save ho gaya", Toast.LENGTH_SHORT).show()
            checkBackend(true)
        }
        findViewById<Button>(R.id.btnRefreshLogs).setOnClickListener { refreshLogs() }

        checkBackend(false)
    }

    private fun show(which: String) {
        web.visibility = if (which == "scan") View.VISIBLE else View.GONE
        setupView.visibility = if (which == "setup") View.VISIBLE else View.GONE
        settingsView.visibility = if (which == "settings") View.VISIBLE else View.GONE
        if (which == "scan") loadScan()
    }

    private fun loadScan() {
        val target = base() + "/scan.html"
        if (web.url != target) web.loadUrl(target)
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = true
            mediaPlaybackRequiresUserGesture = false
        }
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView?, url: String?): Boolean {
                if (url == null) return false
                val host = Uri.parse(url).host ?: ""
                val baseHost = Uri.parse(base()).host ?: ""
                return if (host == baseHost || url.startsWith("blob:")) {
                    false // app ke andar hi kholo
                } else {
                    startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                    true
                }
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                webView: WebView?,
                filePathCallback: ValueCallback<Array<Uri>>?,
                fileChooserParams: FileChooserParams?
            ): Boolean {
                fileCallback = filePathCallback
                val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                    addCategory(Intent.CATEGORY_OPENABLE)
                    type = "image/*"
                }
                startActivityForResult(intent, 1001)
                return true
            }
        }
        web.setDownloadListener { url, _, _, _, _ ->
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == 1001) {
            val result = if (resultCode == Activity.RESULT_OK && data?.data != null)
                arrayOf(data.data!!) else null
            fileCallback?.onReceiveValue(result)
            fileCallback = null
        }
    }

    /** Termux RUN_COMMAND se backend chalao (setup ke baad kaam karta hai). */
    private fun startBackend() {
        try {
            val intent = Intent("com.termux.RUN_COMMAND").apply {
                setClassName("com.termux", "com.termux.app.RunCommandService")
                putExtra("com.termux.RUN_COMMAND_PATH",
                    "/data/data/com.termux/files/usr/bin/bash")
                putExtra("com.termux.RUN_COMMAND_ARGUMENTS",
                    arrayOf("-c", "bash ~/vis/start-backend.sh"))
                putExtra("com.termux.RUN_COMMAND_WORKDIR",
                    "/data/data/com.termux/files/home")
                putExtra("com.termux.RUN_COMMAND_BACKGROUND", true)
            }
            startService(intent)
            Toast.makeText(this, "Backend start ho raha hai — 1 minute baad Check karo",
                Toast.LENGTH_LONG).show()
            setupStatus.text = "⏳ Backend start kiya — model load hone me ~1 minute lag sakta hai. Phir 'Backend Check Karo' dabao."
        } catch (e: Exception) {
            Toast.makeText(this, "Termux nahi mila — pehle Step 1 se Termux install karo",
                Toast.LENGTH_LONG).show()
            setupStatus.text = "❌ Termux nahi mila. Step 1: Termux install karo, Step 2: command chalao."
        }
    }

    private fun httpGet(path: String, timeoutMs: Int = 6000): Pair<Int, String> {
        val conn = URL(base() + path).openConnection() as HttpURLConnection
        conn.connectTimeout = timeoutMs
        conn.readTimeout = timeoutMs
        conn.requestMethod = "GET"
        val code = conn.responseCode
        val body = try {
            conn.inputStream.bufferedReader().readText()
        } catch (e: Exception) {
            try { conn.errorStream?.bufferedReader()?.readText() ?: "" }
            catch (e2: Exception) { "" }
        }
        conn.disconnect()
        return code to body
    }

    private fun checkBackend(manual: Boolean) {
        setupStatus.text = "⏳ Backend check ho raha hai… (${base()})"
        thread {
            val ok = try {
                httpGet("/health", 5000).first == 200 ||
                    httpGet("/scan.html", 5000).first == 200
            } catch (e: Exception) { false }
            runOnUiThread {
                if (ok) {
                    setupStatus.text = "✅ Backend chal raha hai — Scan tab par jao."
                    show("scan")
                } else {
                    setupStatus.text = "❌ Backend nahi mila (${base()}). Neeche ke steps se local environment install/start karo — ya Settings me remote URL dal do."
                    if (!manual) show("setup") else show("setup")
                }
            }
        }
    }

    private fun refreshLogs() {
        logsView.text = "Logs aa rahe hain…"
        thread {
            val text = try {
                val (code, body) = httpGet("/logs?n=200", 8000)
                if (code == 200) {
                    val arr = JSONObject(body).getJSONArray("lines")
                    val sb = StringBuilder()
                    for (i in 0 until arr.length()) sb.append(arr.getString(i)).append('\n')
                    if (arr.length() == 0) "(abhi koi log line nahi — pehle ek scan chalao)"
                    else sb.toString()
                } else "Logs nahi mile (HTTP $code) — backend chal raha hai?"
            } catch (e: Exception) {
                "Logs nahi mile — backend se rabta nahi (${e.message}). Pehle Setup se backend start karo."
            }
            runOnUiThread { logsView.text = text }
        }
    }

    override fun onBackPressed() {
        if (web.visibility == View.VISIBLE && web.canGoBack()) web.goBack()
        else super.onBackPressed()
    }
}
