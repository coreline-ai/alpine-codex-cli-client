plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

val releaseStoreFile = providers.environmentVariable("ALPINE_RELEASE_STORE_FILE")
val releaseStorePassword = providers.environmentVariable("ALPINE_RELEASE_STORE_PASSWORD")
val releaseKeyAlias = providers.environmentVariable("ALPINE_RELEASE_KEY_ALIAS")
val releaseKeyPassword = providers.environmentVariable("ALPINE_RELEASE_KEY_PASSWORD")
val releaseSigningValues = listOf(
    releaseStoreFile.orNull,
    releaseStorePassword.orNull,
    releaseKeyAlias.orNull,
    releaseKeyPassword.orNull,
)
val hasAnyReleaseSigningInput = releaseSigningValues.any { !it.isNullOrBlank() }
val hasCompleteReleaseSigningInputs = releaseSigningValues.all { !it.isNullOrBlank() }
check(!hasAnyReleaseSigningInput || hasCompleteReleaseSigningInputs) {
    "Release signing requires all four ALPINE_RELEASE_* environment variables"
}

android {
    namespace = "dev.alpine.codexclient"
    compileSdk = 36

    defaultConfig {
        applicationId = "dev.alpine.codexclient"
        minSdk = 26
        targetSdk = 36
        versionCode = 2
        versionName = "0.2.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        manifestPlaceholders["appLabel"] = "Alpine Agent Client"
        ndk { abiFilters += "arm64-v8a" }
    }

    signingConfigs {
        if (hasCompleteReleaseSigningInputs) {
            create("externalRelease") {
                storeFile = rootProject.file(releaseStoreFile.get())
                storePassword = releaseStorePassword.get()
                keyAlias = releaseKeyAlias.get()
                keyPassword = releaseKeyPassword.get()
            }
        }
    }

    buildTypes {
        getByName("debug") {
            applicationIdSuffix = ".labdebug"
            versionNameSuffix = "-labdebug"
            manifestPlaceholders["appLabel"] = "Alpine Agent Client (Lab)"
            buildConfigField("boolean", "ALLOW_REAL_OAUTH", "false")
        }
        create("secureDebug") {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-secure-debug"
            isDebuggable = false
            signingConfig = signingConfigs.getByName("debug")
            matchingFallbacks += listOf("debug")
            manifestPlaceholders["appLabel"] = "Alpine Agent Client"
            buildConfigField("boolean", "ALLOW_REAL_OAUTH", "true")
        }
        getByName("release") {
            isDebuggable = false
            isMinifyEnabled = false
            manifestPlaceholders["appLabel"] = "Alpine Agent Client"
            buildConfigField("boolean", "ALLOW_REAL_OAUTH", "true")
            if (hasCompleteReleaseSigningInputs) {
                signingConfig = signingConfigs.getByName("externalRelease")
            }
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    // The installer copies the bundled rootfs as an asset and executes PRoot
    // from the native-library directory inside the selected app variant.
    androidResources {
        noCompress += "asset"
    }

    sourceSets {
        getByName("main").assets.srcDir(layout.buildDirectory.dir("generated/distribution/assets/audit"))
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

val componentInventory = layout.buildDirectory.file(
    "generated/distribution/assets/audit/META-INF/alpine-codex/component-inventory.json",
)
val pythonPackagePackAssets = rootProject.layout.projectDirectory.dir(
    "alpine-python-pack-bundled/build/generated/distribution/assets/alpine-python-pack",
)

val generateComponentInventory by tasks.registering(Exec::class) {
    group = "verification"
    description = "Generates the shared component, license, and SBOM inventory asset."
    inputs.file(rootProject.layout.projectDirectory.file("gradle/libs.versions.toml"))
    inputs.file(rootProject.layout.projectDirectory.file("codex-cli-pack/codex-cli.lock.json"))
    inputs.file(rootProject.layout.projectDirectory.file("grok-cli-pack/grok-cli.lock.json"))
    inputs.file(rootProject.layout.projectDirectory.file(
        "alpine-runtime-pack-bundled/src/main/resources/META-INF/alpine-runtime/sbom.spdx.json",
    ))
    inputs.file(rootProject.layout.projectDirectory.file("scripts/generate-component-inventory.py"))
    inputs.dir(pythonPackagePackAssets)
    outputs.file(componentInventory)
    dependsOn(":alpine-python-pack-bundled:preparePythonPackagePackAssets")
    commandLine(
        "python3",
        rootProject.layout.projectDirectory.file("scripts/generate-component-inventory.py").asFile.absolutePath,
        "--project-root",
        rootProject.layout.projectDirectory.asFile.absolutePath,
        "--output",
        componentInventory.get().asFile.absolutePath,
        "--python-pack-assets",
        pythonPackagePackAssets.asFile.absolutePath,
    )
}

val verifyReleaseSigningInputs by tasks.registering {
    group = "verification"
    description = "Fails release packaging unless external signing inputs are complete and readable."
    doLast {
        check(hasCompleteReleaseSigningInputs) {
            "Public release packaging requires externally supplied ALPINE_RELEASE_* signing inputs"
        }
        val configuredStore = rootProject.file(releaseStoreFile.get())
        check(configuredStore.isFile && configuredStore.canRead()) {
            "ALPINE_RELEASE_STORE_FILE is not a readable file"
        }
    }
}

val verifyReleasePythonPackagePack by tasks.registering {
    group = "verification"
    description = "Fails public release packaging unless an APK-contained production Python pack exists."
    dependsOn(":alpine-python-pack-bundled:verifyProductionPythonPackagePack")
}

tasks.configureEach {
    if (
        (name.startsWith("merge") && name.endsWith("Assets")) ||
        name.lowercase().contains("lint")
    ) {
        dependsOn(generateComponentInventory)
    }
    if (
        name in setOf(
            "assembleRelease",
            "bundleRelease",
            "packageRelease",
            "packageReleaseBundle",
            "packageReleaseUniversalApk",
            "makeApkFromBundleForRelease",
            "extractApksFromBundleForRelease",
            "signReleaseBundle",
        )
    ) {
        dependsOn(verifyReleaseSigningInputs)
        dependsOn(verifyReleasePythonPackagePack)
    }
}

dependencies {
    implementation(project(":alpine-runtime-api"))
    implementation(project(":alpine-runtime-android"))
    implementation(project(":alpine-runtime-host"))
    implementation(project(":alpine-runtime-background-android"))
    implementation(project(":alpine-runtime-ui-compose"))
    implementation(project(":alpine-runtime-pack-bundled"))
    implementation(project(":alpine-python-pack-bundled"))
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

    testImplementation("junit:junit:4.13.2")

    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.test.ext.junit)
    androidTestImplementation(libs.androidx.test.espresso.core)
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)

    // Compose instrumentation needs its target-process idling/semantics bridge in the
    // lab APK. The secureDebug configurations below explicitly exclude this artifact.
    debugImplementation(libs.androidx.compose.ui.test.manifest)
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
        jvmDefault.set(org.jetbrains.kotlin.gradle.dsl.JvmDefaultMode.DISABLE)
    }
}

configurations.matching {
    it.name.startsWith("secureDebug", ignoreCase = true) ||
        it.name.startsWith("release", ignoreCase = true)
}.configureEach {
    exclude(group = "androidx.compose.ui", module = "ui-test-manifest")
    exclude(group = "androidx.compose.ui", module = "ui-tooling")
}
