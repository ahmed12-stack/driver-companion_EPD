plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.drivercompanion.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.drivercompanion.app"
        minSdk = 26
        targetSdk = 34
        versionCode = 4
        versionName = "0.3.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
    }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.09.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.animation:animation")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.core:core-ktx:1.13.1")
    // Google's fused location (GPS + Wi-Fi + towers), as Maps uses
    implementation("com.google.android.gms:play-services-location:21.3.0")
    // OpenStreetMap map for trip routes (no API key)
    implementation("org.osmdroid:osmdroid-android:6.1.20")
}
