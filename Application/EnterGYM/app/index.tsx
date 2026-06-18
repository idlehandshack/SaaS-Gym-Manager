import { useEffect, useRef } from "react";
import {
  View,
  Text,
  StyleSheet,
  Animated,
  Easing,
  Image,
  Dimensions,
} from "react-native";
import { router } from "expo-router";

const { width } = Dimensions.get("window");

export default function SplashScreenPage() {
  // Individual animation values for staggered effect
  const logoScale   = useRef(new Animated.Value(0.6)).current;
  const logoOpacity = useRef(new Animated.Value(0)).current;
  const titleX      = useRef(new Animated.Value(-40)).current;
  const titleOpacity= useRef(new Animated.Value(0)).current;
  const subOpacity  = useRef(new Animated.Value(0)).current;
  const lineWidth   = useRef(new Animated.Value(0)).current;
  const tagOpacity  = useRef(new Animated.Value(0)).current;
  const overlayOpacity = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    // Staggered entrance sequence
    Animated.sequence([
      // 1. Logo pops in
      Animated.parallel([
        Animated.spring(logoScale, {
          toValue: 1,
          tension: 60,
          friction: 7,
          useNativeDriver: true,
        }),
        Animated.timing(logoOpacity, {
          toValue: 1,
          duration: 400,
          useNativeDriver: true,
        }),
      ]),

      // 2. Accent line grows
      Animated.timing(lineWidth, {
        toValue: 1,
        duration: 350,
        easing: Easing.out(Easing.exp),
        useNativeDriver: true,
      }),

      // 3. Title slides in
      Animated.parallel([
        Animated.timing(titleX, {
          toValue: 0,
          duration: 400,
          easing: Easing.out(Easing.cubic),
          useNativeDriver: true,
        }),
        Animated.timing(titleOpacity, {
          toValue: 1,
          duration: 350,
          useNativeDriver: true,
        }),
      ]),

      // 4. Tag + subtitle fade in
      Animated.parallel([
        Animated.timing(subOpacity, {
          toValue: 1,
          duration: 400,
          useNativeDriver: true,
        }),
        Animated.timing(tagOpacity, {
          toValue: 1,
          duration: 400,
          useNativeDriver: true,
        }),
      ]),
    ]).start();

    // Navigate after 2.8s with fade-out
    const timer = setTimeout(() => {
      Animated.timing(overlayOpacity, {
        toValue: 0,
        duration: 400,
        useNativeDriver: true,
      }).start(() => router.replace("/main"));
    }, 2600);

    return () => clearTimeout(timer);
  }, []);

  return (
    <Animated.View style={[styles.container, { opacity: overlayOpacity }]}>
      {/* Decorative background bars */}
      <View style={styles.barsContainer} pointerEvents="none">
        {[...Array(6)].map((_, i) => (
          <View
            key={i}
            style={[
              styles.bgBar,
              {
                left: 10 + i * 60,
                height: 80 + i * 50,
                opacity: 0.025 + i * 0.008,
              },
            ]}
          />
        ))}
      </View>

      {/* Top accent */}
      <View style={styles.topAccent} />

      {/* Content */}
      <View style={styles.content}>
        {/* Logo */}
        <Animated.View
          style={{
            opacity: logoOpacity,
            transform: [{ scale: logoScale }],
            marginBottom: 32,
          }}
        >
          <View style={styles.logoRing}>
            <Image
              source={require("../assets/splash.png")}
              style={styles.logo}
              resizeMode="contain"
            />
          </View>
        </Animated.View>

        {/* Accent line */}
        <Animated.View
          style={[
            styles.accentLine,
            { transform: [{ scaleX: lineWidth }] },
          ]}
        />

        {/* Title */}
        <Animated.Text
          style={[
            styles.title,
            {
              opacity: titleOpacity,
              transform: [{ translateX: titleX }],
            },
          ]}
        >
          Enter<Text style={styles.titleAccent}>GYM</Text>
        </Animated.Text>

        {/* Tag */}
        <Animated.View style={[styles.tag, { opacity: tagOpacity }]}>
          <View style={styles.tagDot} />
          <Text style={styles.tagText}>EST. 2026</Text>
          <View style={styles.tagDot} />
        </Animated.View>

        {/* Subtitle */}
        <Animated.Text style={[styles.subtitle, { opacity: subOpacity }]}>
          Your Fitness Journey Starts Here
        </Animated.Text>
      </View>

      {/* Bottom branding */}
      <Animated.View style={[styles.bottomRow, { opacity: subOpacity }]}>
        <View style={styles.bottomBar} />
        <Text style={styles.bottomLabel}>POWERED BY EnterGYM</Text>
        <View style={styles.bottomBar} />
      </Animated.View>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#080808",
    justifyContent: "center",
    alignItems: "center",
  },

  topAccent: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    height: 3,
    backgroundColor: "#ff4d00",
  },

  // ── Background bars ──────────────────────────────────────
  barsContainer: {
    position: "absolute",
    inset: 0,
    overflow: "hidden",
  },

  bgBar: {
    position: "absolute",
    bottom: 0,
    width: 32,
    backgroundColor: "#ff4d00",
    borderTopLeftRadius: 4,
    borderTopRightRadius: 4,
  },

  // ── Content ──────────────────────────────────────────────
  content: {
    alignItems: "center",
    paddingHorizontal: 32,
  },

  // ── Logo ─────────────────────────────────────────────────
  logoRing: {
    width: 160,
    height: 160,
    borderRadius: 80,
    borderWidth: 1,
    borderColor: "#ff4d0040",
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: "#ff4d000a",
  },

  logo: {
    width: 160,
    height: 160,
  },

  // ── Accent line ──────────────────────────────────────────
  accentLine: {
    width: 120,
    height: 2,
    backgroundColor: "#ff4d00",
    borderRadius: 1,
    marginBottom: 20,
    transformOrigin: "left",
  },

  // ── Typography ───────────────────────────────────────────
  title: {
    fontSize: 44,
    fontWeight: "900",
    color: "#ffffff",
    letterSpacing: 6,
    marginBottom: 12,
  },

  titleAccent: {
    color: "#ff4d00",
  },

  tag: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginBottom: 12,
  },

  tagDot: {
    width: 4,
    height: 4,
    borderRadius: 2,
    backgroundColor: "#ff4d00",
  },

  tagText: {
    color: "#555",
    fontSize: 10,
    fontWeight: "700",
    letterSpacing: 4,
  },

  subtitle: {
    color: "#666",
    fontSize: 13,
    letterSpacing: 1,
    textAlign: "center",
  },

  // ── Bottom ───────────────────────────────────────────────
  bottomRow: {
    position: "absolute",
    bottom: 48,
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 32,
    width: "100%",
    gap: 12,
  },

  bottomBar: {
    flex: 1,
    height: 1,
    backgroundColor: "#1e1e1e",
  },

  bottomLabel: {
    color: "#2e2e2e",
    fontSize: 9,
    letterSpacing: 3,
    fontWeight: "700",
  },
});