import java.io.File
import java.security.MessageDigest

plugins {
    id("com.android.library")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "dev.alpine.codexclient.gatewaypack"
    compileSdk = 36

    defaultConfig {
        minSdk = 26
        consumerProguardFiles("consumer-rules.pro")
    }

    sourceSets {
        getByName("main").assets.srcDir(layout.buildDirectory.dir("generated/distribution/assets"))
    }

    androidResources {
        noCompress += "asset"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        disable += "AndroidGradlePluginVersion"
    }
}

fun sha256(file: File): String {
    val digest = MessageDigest.getInstance("SHA-256")
    file.inputStream().buffered().use { input ->
        val buffer = ByteArray(64 * 1024)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) break
            digest.update(buffer, 0, count)
        }
    }
    return digest.digest().joinToString("") { byte ->
        (byte.toInt() and 0xff).toString(16).padStart(2, '0')
    }
}

val generatedAssetsDirectory = layout.buildDirectory.dir("generated/distribution/assets/codex-gateway")
val gatewaySourceDirectory = rootProject.layout.projectDirectory.dir("codex_gateway")

val prepareCodexGatewayAssets by tasks.registering {
    group = "build setup"
    description = "Packages the local app-server supervisor for Android app variants."
    inputs.dir(gatewaySourceDirectory)
    outputs.dir(generatedAssetsDirectory)

    doLast {
        val destination = generatedAssetsDirectory.get().asFile
        destination.parentFile.deleteRecursively()
        check(destination.mkdirs()) { "Cannot create generated Codex gateway asset directory" }
        val packageDestination = File(destination, "codex_gateway")
        copy {
            from(gatewaySourceDirectory)
            include("**/*.py")
            into(packageDestination)
        }
        val files = packageDestination.walkTopDown()
            .filter { it.isFile && it.extension == "py" }
            .sortedBy { it.relativeTo(destination).invariantSeparatorsPath }
            .toList()
        check(files.isNotEmpty()) { "Codex gateway source is missing" }
        val entries = files.joinToString(",\n") { file ->
            val path = file.relativeTo(destination).invariantSeparatorsPath
            "    {\"path\":\"$path\",\"size\":${file.length()},\"sha256\":\"${sha256(file)}\"}"
        }
        File(destination, "gateway-manifest.json").writeText("{\n  \"files\": [\n$entries\n  ]\n}\n")
    }
}

tasks.configureEach {
    if (
        (name.startsWith("merge") && name.endsWith("Assets")) ||
        name.lowercase().contains("lint")
    ) {
        dependsOn(prepareCodexGatewayAssets)
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
        jvmDefault.set(org.jetbrains.kotlin.gradle.dsl.JvmDefaultMode.DISABLE)
    }
}
