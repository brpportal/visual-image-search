package com.brpportal.imagescan

import android.annotation.SuppressLint
import android.app.Activity
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import android.view.View
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.brpportal.imagescan.ai.AiClient
import com.brpportal.imagescan.ai.AiConfig
import com.brpportal.imagescan.engine.ModelManager
import com.brpportal.imagescan.engine.ReferencesManager
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/**
 * Image Scan v2 — on-device engine (CLIP ONNX app ke andar),
 * References page, AI (custom base URL + key, optional), Tasks
 * (background automation). Purana Termux/remote backend Scan tab
 * ke liye ab bhi option hai.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var web: WebView
    private lateinit var setupView: View
    private lateinit var settingsView: View
    private lateinit var refsView: View
    private lateinit var aiView: View
    private lateinit var setupStatus: TextView
    private lateinit var editBase: EditText
    private lateinit var logsView: TextView
    private lateinit var modelStatus: TextView
    private lateinit var refsStatus: TextView
    private lateinit var refsList: LinearLayout
    private lateinit var editAiBase: EditText
    private lateinit var editAiKey: EditText
    private lateinit var editAiModel: EditText
    private lateinit var aiStatus: TextView

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
        refsView = findViewById(R.id.refsView)
        aiView = findViewById(R.id.aiView)
        setupStatus = findViewById(R.id.setupStatus)
        editBase = findViewById(R.id.editBase)
        logsView = findViewById(R.id.logsView)
        modelStatus = findViewById(R.id.modelStatus)
        refsStatus = findViewById(R.id.refsStatus)
        refsList = findViewById(R.id.refsList)
        editAiBase = findViewById(R.id.editAiBase)
        editAiKey = findViewById(R.id.editAiKey)
        editAiModel = findViewById(R.id.editAiModel)
        aiStatus = findViewById(R.id.aiStatus)
        editBase.setText(base())

        val aiCfg = AiConfig(this)
        editAiBase.setText(aiCfg.baseUrl)
        editAiKey.setText(aiCfg.apiKey)
        editAiModel.setText(aiCfg.model)

        setupWebView()

        findViewById<Button>(R.id.navScan).setOnClickListener { show("scan") }
        findViewById<Button>(R.id.navRefs).setOnClickListener { show("refs") }
        findViewById<Button>(R.id.navAi).setOnClickListener { show("ai") }
        findViewById<Button>(R.id.navSetup).setOnClickListener { show("setup") }
        findViewById<Button>(R.id.navSettings).setOnClickListener { show("settings") }

        findViewById<Button>(R.id.btnDownloadModel).setOnClickListener { downloadModel() }
        findViewById<Button>(R.id.btnAddRef).setOnClickListener { pickRefs() }

        findViewById<Button>(R.id.btnPresetRouter).setOnClickListener {
            editAiBase.setText("https://openrouter.ai/api/v1")
        }
        findViewById<Button>(R.id.btnPresetApmix).setOnClickListener {
            editAiBase.setText("https://api.apmix.ai/v1")
        }
        findViewById<Button>(R.id.btnPresetOpenAi).setOnClickListener {
            editAiBase.setText("https://api.openai.com/v1")
        }
        findViewById<Button>(R.id.btnAiSave).setOnClickListener {
            val c = AiConfig(this)
            c.baseUrl = editAiBase.text.toString()
            c.apiKey = editAiKey.text.toString()
            c.model = editAiModel.text.toString()
            editAiKey.setText(c.apiKey) // sanitized version dikha do
            aiStatus.text = if (c.isSet) "✔ AI setting save ho gayi."
            else "⚠ Kuch khali hai — Base URL, Key aur Model teeno chahiye."
            Toast.makeText(this, "AI setting save ho gayi", Toast.LENGTH_SHORT).show()
        }
        findViewById<Button>(R.id.btnAiFetch).setOnClickListener { fetchAiModels() }
        findViewById<Button>(R.id.btnAiTest).setOnClickListener { testAi() }

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
        // v2 ka asal ghar: model na ho to References tab kholo
        if (!ModelManager.isReady(this)) show("refs") else refreshRefs()
    }

    private fun show(which: String) {
        web.visibility = if (which == "scan") View.VISIBLE else View.GONE
        setupView.visibility = if (which == "setup") View.VISIBLE else View.GONE
        settingsView.visibility = if (which == "settings") View.VISIBLE else View.GONE
        refsView.visibility = if (which == "refs") View.VISIBLE else View.GONE
        aiView.visibility = if (which == "ai") View.VISIBLE else View.GONE
        if (which == "scan") loadScan()
        if (which == "refs") refreshRefs()
    }

    // ---------- v2: on-device model + references ----------

    private fun downloadModel() {
        modelStatus.text = "⬇ Download shuru… Wi-Fi par raho, app band mat karna."
        thread {
            try {
                ModelManager.download(this) { msg ->
                    runOnUiThread { modelStatus.text = msg }
                }
                runOnUiThread {
                    modelStatus.text = ModelManager.statusText(this)
                    Toast.makeText(this, "Model ready hai ✔", Toast.LENGTH_LONG).show()
                }
            } catch (e: Exception) {
                runOnUiThread {
                    modelStatus.text = "❌ Download fail: ${e.message}. Internet check karke dobara try karo."
                }
            }
        }
    }

    private fun pickRefs() {
        if (!ModelManager.isReady(this)) {
            Toast.makeText(
                this,
                "Pehle model download karo — embedding usi se banti hai",
                Toast.LENGTH_LONG
            ).show()
            return
        }
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "image/*"
            putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
        }
        startActivityForResult(intent, 1002)
    }

    private fun displayName(uri: Uri): String {
        try {
            contentResolver.query(uri, null, null, null, null)?.use { c ->
                val idx = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (idx >= 0 && c.moveToFirst()) return c.getString(idx)
            }
        } catch (e: Exception) {
            // ignore — fallback neeche
        }
        return uri.lastPathSegment ?: "image"
    }

    private fun decodeDownsampled(uri: Uri): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        contentResolver.openInputStream(uri)?.use {
            BitmapFactory.decodeStream(it, null, bounds)
        }
        var sample = 1
        while (maxOf(bounds.outWidth, bounds.outHeight) / sample > 2048) sample *= 2
        val opts = BitmapFactory.Options().apply { inSampleSize = sample }
        return contentResolver.openInputStream(uri)?.use {
            BitmapFactory.decodeStream(it, null, opts)
        }
    }

    private fun addRefs(uris: List<Uri>) {
        refsStatus.text = "⏳ ${uris.size} images add ho rahi hain (embedding ban rahi hai)…"
        thread {
            val mgr = ReferencesManager(this)
            var ok = 0
            for (uri in uris) {
                try {
                    val bmp = decodeDownsampled(uri)
                    if (bmp != null) {
                        mgr.add(bmp, displayName(uri))
                        ok++
                        val done = ok
                        runOnUiThread {
                            refsStatus.text = "⏳ $done / ${uris.size} add ho gayi…"
                        }
                    }
                } catch (e: Exception) {
                    // ek kharab image baaki ko na roke
                }
            }
            runOnUiThread {
                Toast.makeText(this, "$ok references add ho gayi ✔", Toast.LENGTH_LONG).show()
                refreshRefs()
            }
        }
    }

    private fun refreshRefs() {
        modelStatus.text = ModelManager.statusText(this)
        val mgr = ReferencesManager(this)
        val refs = mgr.list()
        val embedded = refs.count { it.hasVector }
        refsStatus.text = "References: ${refs.size} (embedded: $embedded)"
        refsList.removeAllViews()
        for (ref in refs) {
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                setPadding(0, 10, 0, 10)
            }
            val label = TextView(this).apply {
                text = "${if (ref.hasVector) "✔" else "✗"} ${ref.name}"
                setTextColor(0xFFEEF2F7.toInt())
                textSize = 13.5f
                layoutParams = LinearLayout.LayoutParams(
                    0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f
                )
            }
            val del = Button(this).apply {
                text = "🗑 Delete"
                setOnClickListener {
                    ReferencesManager(this@MainActivity).delete(ref.id)
                    refreshRefs()
                }
            }
            row.addView(label)
            row.addView(del)
            refsList.addView(row)
        }
    }

    // ---------- v2: AI ----------

    private fun fetchAiModels() {
        aiStatus.text = "⏳ Models aa rahe hain…"
        thread {
            try {
                val models = AiClient(this).fetchModels()
                runOnUiThread {
                    aiStatus.text = "✔ ${models.size} models mile. Model field me in me se ek likho:\n" +
                        models.take(30).joinToString("\n")
                }
            } catch (e: Exception) {
                runOnUiThread { aiStatus.text = "❌ Models nahi mile: ${e.message}" }
            }
        }
    }

    private fun testAi() {
        aiStatus.text = "⏳ AI se rabta ho raha hai…"
        thread {
            try {
                val reply = AiClient(this).test()
                runOnUiThread { aiStatus.text = "✔ AI jawab: $reply" }
            } catch (e: Exception) {
                runOnUiThread { aiStatus.text = "❌ AI test fail: ${e.message}" }
            }
        }
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
        if (requestCode == 1002 && resultCode == Activity.RESULT_OK && data != null) {
            val uris = mutableListOf<Uri>()
            data.clipData?.let { cd ->
                for (i in 0 until cd.itemCount) uris.add(cd.getItemAt(i).uri)
            }
            if (uris.isEmpty() && data.data != null) uris.add(data.data!!)
            if (uris.isNotEmpty()) addRefs(uris)
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
                } else {
                    setupStatus.text = "Backend nahi mila (${base()}) — v2 me is ki zaroorat nahi: engine app ke andar hai (References tab). Ye sirf purane Termux tareeqe ke liye hai."
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
