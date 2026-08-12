plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

android {
    namespace = "dev.alpine.codexclient"
    compileSdk = 36

    defaultConfig {
        applicationId = "dev.alpine.codexclient"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        manifestPlaceholders["appLabel"] = "Alpine Agent Client"
        ndk { abiFilters += "arm64-v8a" }
    }

    buildTypes {
        getByName("debug") {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
            manifestPlaceholders["appLabel"] = "Alpine Agent Client (Debug)"
        }
    }

    buildFeatures {
        compose = true
    }

    // The installer copies the bundled rootfs as an asset and executes PRoot
    // from the native-library directory inside this debug-only APK.
    androidResources {
        noCompress += "asset"
    }

    sourceSets {
        getByName("debug").assets.srcDir(layout.buildDirectory.dir("generated/debug/assets/audit"))
    }

    packaging {
        jniLibs {
            useLegacyPackaging = true
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        disable += "AndroidGradlePluginVersion"
    }
}

val debugComponentInventory = layout.buildDirectory.file(
    "generated/debug/assets/audit/META-INF/alpine-codex/debug-component-inventory.json",
)

val generateDebugComponentInventory by tasks.registering(Exec::class) {
    group = "verification"
    description = "Generates the debug-only component, license, and SBOM inventory asset."
    inputs.file(rootProject.layout.projectDirectory.file("gradle/libs.versions.toml"))
    inputs.file(rootProject.layout.projectDirectory.file("codex-cli-pack/codex-cli.lock.json"))
    inputs.file(rootProject.layout.projectDirectory.file("grok-cli-pack/grok-cli.lock.json"))
    inputs.file(rootProject.layout.projectDirectory.file(
        "alpine-runtime-pack-bundled/src/main/resources/META-INF/alpine-runtime/sbom.spdx.json",
    ))
    inputs.file(rootProject.layout.projectDirectory.file("scripts/generate-debug-component-inventory.py"))
    outputs.file(debugComponentInventory)
    commandLine(
        "python3",
        rootProject.layout.projectDirectory.file("scripts/generate-debug-component-inventory.py").asFile.absolutePath,
        "--project-root",
        rootProject.layout.projectDirectory.asFile.absolutePath,
        "--output",
        debugComponentInventory.get().asFile.absolutePath,
    )
}

tasks.configureEach {
    if (name == "mergeDebugAssets" || (name.contains("Debug") && name.lowercase().contains("lint"))) {
        dependsOn(generateDebugComponentInventory)
    }
}

dependencies {
    implementation(project(":alpine-runtime-api"))
    implementation(project(":alpine-runtime-android"))
    implementation(project(":alpine-runtime-host"))
    implementation(project(":alpine-runtime-background-android"))
    implementation(project(":alpine-runtime-ui-compose"))
    implementation(project(":alpine-runtime-pack-bundled"))
    implementation(project(":alpine-workspace-api"))
    implementation(project(":alpine-workspace-android"))
    implementation(project(":codex-cli-pack"))
    implementation(project(":grok-cli-pack"))
    implementation(project(":codex-gateway-pack-bundled"))
    implementation(project(":codex-runtime-bridge"))

    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.compose.foundation)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.ktx)
    implementation(libs.kotlinx.coroutines.android)

    debugImplementation(libs.androidx.compose.ui.tooling)
    debugImplementation(libs.androidx.compose.ui.test.manifest)

    testImplementation("junit:junit:4.13.2")

    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.test.ext.junit)
    androidTestImplementation(libs.androidx.test.espresso.core)
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
        jvmDefault.set(org.jetbrains.kotlin.gradle.dsl.JvmDefaultMode.DISABLE)
    }
}
