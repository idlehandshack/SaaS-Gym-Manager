import 'dotenv/config';
export default {
  expo: {
    name: "EnterGYM",
    slug: "entergym",
    scheme: "entergym",
    version: "1.0.0",
    orientation: "portrait",
    icon: "./assets/icon.png",
    userInterfaceStyle: "dark",

    android: {
      adaptiveIcon: {
        foregroundImage: "./assets/adaptive-icon.png",
        backgroundColor: "#0a0a0a"
      },
      package: "com.entergym.app",
      googleServicesFile: "./google-services.json",
      permissions: [
        "ACCESS_FINE_LOCATION",
        "ACCESS_COARSE_LOCATION",
        "ACCESS_BACKGROUND_LOCATION",
        "RECEIVE_BOOT_COMPLETED",
        "VIBRATE"
      ]
    },

    plugins: [
      "expo-dev-client",
      [
        "expo-splash-screen",
        {
          backgroundColor: "#080808",
          image: "./assets/splash.png",
          imageWidth: 200,
          resizeMode: "contain"
        }
      ],
      "expo-web-browser",
      [
        "expo-build-properties",
        {
          android: {
            usesCleartextTraffic: true
          }
        }
      ],
      "expo-font",
      "expo-router",
      [
        "expo-location",
        {
          locationAlwaysAndWhenInUsePermission:
            "EnterGYM needs your location to automatically mark attendance when you arrive at the gym.",
          locationWhenInUsePermission:
            "EnterGYM needs your location to automatically mark attendance when you arrive at the gym.",
          isAndroidBackgroundLocationEnabled: true
        }
      ],
      [
        "expo-notifications",
        {
          icon: "./assets/icon.png",
          color: "#ff4d00"
        }
      ]
    ],

    extra: {
      // ✅
      apiKey: process.env.INTERNAL_API_KEY,
      eas: {
        projectId: "4ccca9d2-1426-4a48-b0e6-f9c50231962a"
      }
    },

    owner: "azzy03s-organization"
  }
};