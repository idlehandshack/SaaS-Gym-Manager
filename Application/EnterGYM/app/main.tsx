// app/index.tsx  (MainScreen)

import { useCallback, useEffect, useRef, useState } from "react";
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  BackHandler,
  Image,
  Animated,
  Easing,
  PanResponder,
} from "react-native";
import { WebView } from "react-native-webview";
import NetInfo from "@react-native-community/netinfo";
import { SafeAreaView } from "react-native-safe-area-context";
import * as Location from "expo-location";
import * as Haptics from "expo-haptics";
import { useOwnerNotifications } from "../hooks/useOwnerNotifications";
import { useUserDeviceToken } from "../hooks/useUserDeviceToken";
import * as ExpoDevice from "expo-device";
import Svg, { Circle, Defs, RadialGradient, Stop } from "react-native-svg";

const WEBSITE_URL = "https://entergym.onrender.com";
const MIN_SPLASH_DURATION = 1500;
const SAFETY_TIMEOUT = 12000;
const FAB_HIDE_DELAY = 5000; // hide after 5s of no touch

// ── BackgroundBars ───────────────────────────────────────────────
function BackgroundBars() {
  return (
    <View style={styles.barsContainer} pointerEvents="none">
      {BAR_CONFIGS.map((cfg, i) => (
        <View key={i} style={[styles.bar, cfg]} />
      ))}
    </View>
  );
}

const BAR_CONFIGS = [...Array(5)].map((_, i) => ({
  left: `${10 + i * 18}%` as `${number}%`,
  height: `${30 + i * 12}%` as `${number}%`,
  opacity: 0.04 + i * 0.012,
}));

// ── OverlayScreen ────────────────────────────────────────────────
interface OverlayScreenProps {
  title: string;
  subtitle?: string;
  showRetry?: boolean;
  retryLabel?: string;
  onRetry?: () => void;
}

function OverlayScreen({
  title,
  subtitle,
  showRetry = false,
  retryLabel = "Retry",
  onRetry,
}: OverlayScreenProps) {
  const fadeIn = useRef(new Animated.Value(0)).current;
  const slideUp = useRef(new Animated.Value(30)).current;

  useEffect(() => {
    Animated.parallel([
      Animated.timing(fadeIn, {
        toValue: 1,
        duration: 600,
        useNativeDriver: true,
      }),
      Animated.timing(slideUp, {
        toValue: 0,
        duration: 500,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
    ]).start();
  }, [fadeIn, slideUp]);

  return (
    <View style={styles.overlayContainer}>
      <BackgroundBars />
      <View style={styles.accentLine} />
      <Animated.View
        style={[
          styles.overlayContent,
          { opacity: fadeIn, transform: [{ translateY: slideUp }] },
        ]}
      >
        <View style={styles.dinoWrapper}>
          <Image
            source={require("../assets/Loading.gif")}
            style={styles.dino}
            resizeMode="contain"
          />
        </View>
        <View style={styles.tag}>
          <Text style={styles.tagText}>EnterGYM</Text>
        </View>
        <Text style={styles.overlayTitle}>{title}</Text>
        {subtitle ? (
          <Text style={styles.overlaySubtitle}>{subtitle}</Text>
        ) : null}
        {showRetry ? (
          <TouchableOpacity
            style={styles.retryButton}
            onPress={onRetry}
            activeOpacity={0.8}
          >
            <View style={styles.retryButtonInner}>
              <Text style={styles.retryButtonText}>{retryLabel}</Text>
            </View>
          </TouchableOpacity>
        ) : null}
      </Animated.View>
      <View style={styles.bottomDecor}>
        <View style={styles.bottomBar} />
        <Text style={styles.bottomLabel}>POWERED BY EnterGYM</Text>
        <View style={styles.bottomBar} />
      </View>
    </View>
  );
}

// ── TorusRing SVG ────────────────────────────────────────────────
// Mimics the 3-D torus from the GIF using two offset arcs + a shadow circle
interface TorusRingProps {
  size: number;
  color: string;
  spinning: boolean;
  spinAnim: Animated.Value;
  isDone: boolean;
  isBack: boolean;
}

function TorusRing({
  size,
  color,
  spinning,
  spinAnim,
  isDone,
  isBack,
}: TorusRingProps) {
  const R = size / 2;
  const outerR = R - 4;
  const innerR = R * 0.42;
  const shadowOffset = R * 0.14;

  const rotate = spinAnim.interpolate({
    inputRange: [0, 1],
    outputRange: ["0deg", "360deg"],
  });

  const ringColor = isDone ? "#22c55e" : isBack ? "#ff4d0055" : color;
  const shadowColor = isDone ? "#22c55e44" : "#ff4d0033";

  return (
    <Animated.View
      style={[
        { width: size, height: size },
        spinning && { transform: [{ rotate }] },
      ]}
    >
      <Svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <Defs>
          <RadialGradient id="torusShadow" cx="55%" cy="62%" r="50%">
            <Stop offset="0%" stopColor={shadowColor} stopOpacity="0.7" />
            <Stop offset="100%" stopColor={shadowColor} stopOpacity="0" />
          </RadialGradient>
        </Defs>

        {/* 3-D shadow layer — offset circle creates depth illusion */}
        <Circle
          cx={R + shadowOffset * 0.6}
          cy={R + shadowOffset}
          r={outerR - 2}
          fill="none"
          stroke={shadowColor}
          strokeWidth={size * 0.22}
          opacity={0.5}
        />

        {/* Main torus body */}
        <Circle
          cx={R}
          cy={R}
          r={outerR}
          fill="none"
          stroke={ringColor}
          strokeWidth={size * 0.22}
          opacity={isDone ? 1 : isBack ? 0.35 : 0.92}
        />

        {/* Inner highlight arc — top-left quadrant only, creates 3-D shine */}
        <Circle
          cx={R}
          cy={R}
          r={outerR}
          fill="none"
          stroke="#ffffff"
          strokeWidth={size * 0.07}
          strokeDasharray={`${outerR * Math.PI * 0.38} ${outerR * Math.PI * 1.62}`}
          strokeDashoffset={outerR * Math.PI * 0.28}
          strokeLinecap="round"
          opacity={isDone ? 0.4 : 0.18}
        />

        {/* Punch-out centre hole */}
        <Circle cx={R} cy={R} r={innerR} fill="#080808" />
      </Svg>
    </Animated.View>
  );
}

// ── TorusFAB ─────────────────────────────────────────────────────
type FabState = "idle" | "refreshing" | "done" | "back";

interface TorusFABProps {
  onPress: () => void;
  onLongPress: () => void;
  isRefreshing: boolean;
  visible: boolean; // controlled by parent (show/hide)
  userVisible: boolean; // controlled by touch-idle timer
  loadProgress: number;
  canGoBack: boolean;
}

function TorusFAB({
  onPress,
  onLongPress,
  isRefreshing,
  visible,
  userVisible,
  loadProgress,
  canGoBack,
}: TorusFABProps) {
  const [fabState, setFabState] = useState<FabState>("idle");

  const scaleAnim = useRef(new Animated.Value(0)).current;
  const opacityAnim = useRef(new Animated.Value(0)).current;
  const spinAnim = useRef(new Animated.Value(0)).current;
  const doneScale = useRef(new Animated.Value(0.5)).current;
  const doneOpacity = useRef(new Animated.Value(0)).current;
  const labelOpacity = useRef(new Animated.Value(1)).current;

  const spinLoop = useRef<Animated.CompositeAnimation | null>(null);

  // ── Show / hide based on visibility + userVisible ──────────────
  useEffect(() => {
    const shouldShow = visible && userVisible;
    Animated.parallel([
      Animated.spring(scaleAnim, {
        toValue: shouldShow ? 1 : 0.7,
        tension: 100,
        friction: 8,
        useNativeDriver: true,
      }),
      Animated.timing(opacityAnim, {
        toValue: shouldShow ? 1 : 0,
        duration: shouldShow ? 250 : 400,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
    ]).start();
  }, [visible, userVisible]);

  // ── Spin / done state machine ──────────────────────────────────
  useEffect(() => {
    if (isRefreshing) {
      setFabState("refreshing");
      spinAnim.setValue(0);
      spinLoop.current = Animated.loop(
        Animated.timing(spinAnim, {
          toValue: 1,
          duration: 1800,
          easing: Easing.linear,
          useNativeDriver: true,
        }),
      );
      spinLoop.current.start();
    } else {
      spinLoop.current?.stop();

      // Flash done
      setFabState("done");
      doneScale.setValue(0.5);
      doneOpacity.setValue(0);

      Animated.parallel([
        Animated.spring(doneScale, {
          toValue: 1,
          tension: 200,
          friction: 6,
          useNativeDriver: true,
        }),
        Animated.timing(doneOpacity, {
          toValue: 1,
          duration: 180,
          useNativeDriver: true,
        }),
      ]).start();

      setTimeout(() => {
        Animated.timing(doneOpacity, {
          toValue: 0,
          duration: 400,
          useNativeDriver: true,
        }).start(() => {
          doneScale.setValue(0.5);
          setFabState(canGoBack ? "back" : "idle");
        });
      }, 900);
    }

    return () => spinLoop.current?.stop();
  }, [isRefreshing]); // eslint-disable-line react-hooks/exhaustive-deps

  // canGoBack transitions outside of load cycle
  useEffect(() => {
    if (fabState === "refreshing" || fabState === "done") return;
    setFabState(canGoBack ? "back" : "idle");
  }, [canGoBack]); // eslint-disable-line react-hooks/exhaustive-deps

  const isRefreshingNow = fabState === "refreshing";
  const isDone = fabState === "done";
  const isBack = fabState === "back";

  const handlePress = () => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    onPress();
  };
  const handleLongPress = () => {
    if (!canGoBack) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy);
    onLongPress();
  };

  const SIZE = 64;
  const color = "#ff4d00";

  // Label text
  const label = isRefreshingNow
    ? `${Math.round(loadProgress * 100)}%`
    : isDone
      ? "✓"
      : isBack
        ? "‹ back"
        : "↻";

  const labelColor = isDone ? "#22c55e" : isBack ? "#ff4d0066" : "#ff4d00";

  return (
    <Animated.View
      pointerEvents={visible ? "auto" : "none"}
      style={[
        styles.fabContainer,
        {
          opacity: opacityAnim,
          transform: [{ scale: scaleAnim }],
        },
      ]}
    >
      <TouchableOpacity
        onPress={handlePress}
        onLongPress={handleLongPress}
        delayLongPress={400}
        activeOpacity={0.75}
        disabled={isRefreshingNow}
      >
        {/* Done checkmark pop — rendered above ring */}
        <View
          style={{
            width: SIZE,
            height: SIZE,
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <TorusRing
            size={SIZE}
            color={color}
            spinning={isRefreshingNow}
            spinAnim={spinAnim}
            isDone={isDone}
            isBack={isBack}
          />

          {/* Centre label */}
          <View style={StyleSheet.absoluteFill} pointerEvents="none">
            <View
              style={{
                flex: 1,
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Animated.Text
                style={{
                  color: labelColor,
                  fontSize: isBack
                    ? 11
                    : isDone
                      ? 17
                      : isRefreshingNow
                        ? 10
                        : 18,
                  fontWeight: "800",
                  opacity: labelOpacity,
                  letterSpacing: isBack ? 0.5 : 0,
                }}
              >
                {label}
              </Animated.Text>
            </View>
          </View>
        </View>

        {/* Pill hint below ring */}
        <View
          style={[
            styles.torusPill,
            isRefreshingNow && styles.torusPillActive,
            isDone && styles.torusPillDone,
          ]}
        >
          <Text
            style={[
              styles.torusPillText,
              isRefreshingNow && styles.torusPillTextActive,
              isDone && styles.torusPillTextDone,
            ]}
          >
            {isRefreshingNow
              ? "LOADING"
              : isDone
                ? "DONE"
                : isBack
                  ? "HOLD · BACK"
                  : "REFRESH"}
          </Text>
        </View>
      </TouchableOpacity>
    </Animated.View>
  );
}

// ── MainScreen ───────────────────────────────────────────────────
export default function MainScreen() {
  const webViewRef = useRef<WebView>(null);
  const [canGoBack, setCanGoBack] = useState(false);
  const [loading, setLoading] = useState(true);
  const [hasError, setHasError] = useState(false);
  const [isConnected, setIsConnected] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [showFab, setShowFab] = useState(false);
  const [userVisible, setUserVisible] = useState(true); // touch-idle visibility
  const [loadProgress, setLoadProgress] = useState(0);

  const fabTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const idleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const loadingFade = useRef(new Animated.Value(1)).current;
  const loadStartTime = useRef<number>(Date.now());
  const safetyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const bgLocationRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useOwnerNotifications();
  const userDeviceToken = useUserDeviceToken();
  // ── Touch-idle hide logic ──────────────────────────────────────
  const resetIdleTimer = useCallback(() => {
    setUserVisible(true);
    if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
    idleTimerRef.current = setTimeout(() => {
      setUserVisible(false);
    }, FAB_HIDE_DELAY);
  }, []);

  // PanResponder that sits over the whole screen (pointerEvents="none" on capture)
  // just to detect any touch without swallowing WebView events
  const panResponder = useRef(
    PanResponder.create({
      onStartShouldSetPanResponder: () => false, // never claim the gesture
      onMoveShouldSetPanResponder: () => false,
      onStartShouldSetPanResponderCapture: () => {
        resetIdleTimer(); // side-effect only
        return false; // pass through to WebView
      },
      onMoveShouldSetPanResponderCapture: () => {
        resetIdleTimer();
        return false;
      },
    }),
  ).current;

  // Start idle timer once FAB appears
  useEffect(() => {
    if (showFab) resetIdleTimer();
    return () => {
      if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
    };
  }, [showFab, resetIdleTimer]);

  // ── Location ───────────────────────────────────────────────────
  const injectLocation = useCallback(async () => {
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== "granted") return;
      const loc = await Location.getCurrentPositionAsync({
        accuracy: Location.Accuracy.Balanced,
      });
      const { latitude, longitude } = loc.coords;
      const js = `
        (function() {
          try {
            localStorage.setItem('gym_location', JSON.stringify({ lat: ${latitude}, lng: ${longitude}, ts: ${Date.now()} }));
            localStorage.setItem('gym_loc_asked', 'granted');
            if (navigator.serviceWorker && navigator.serviceWorker.controller) {
              navigator.serviceWorker.controller.postMessage({ type: 'CACHE_LOC', lat: ${latitude}, lng: ${longitude} });
            }
          } catch(e) {}
        })();
        true;
      `;
      webViewRef.current?.injectJavaScript(js);
    } catch (e) {}
  }, []);

  const injectDeviceToken = useCallback(() => {
    if (!userDeviceToken) return;

    const deviceName =
      `${ExpoDevice.brand ?? ""} ${ExpoDevice.modelName ?? ""}`.trim() ||
      "Unknown";

    const js = `
    (function() {
      try {
        function getCookie(name) {
          var match = document.cookie.match('(^|;\\\\s*)' + name + '=([^;]*)');
          return match ? decodeURIComponent(match[2]) : null;
        }
        var csrftoken = getCookie('csrftoken');
        fetch('/user-devices/register/', {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrftoken || ''
          },
          body: JSON.stringify({
            token: ${JSON.stringify(userDeviceToken)},
            device_name: ${JSON.stringify(deviceName)}
          })
        }).catch(function(){});
      } catch(e) {}
    })();
    true;
  `;
    webViewRef.current?.injectJavaScript(js);
  }, [userDeviceToken]);
  const startLocationPolling = useCallback(async () => {
    const { status } = await Location.requestForegroundPermissionsAsync();
    if (status !== "granted") return;
    await injectLocation();
    bgLocationRef.current = setInterval(injectLocation, 30_000);
  }, [injectLocation]);

  useEffect(() => {
    startLocationPolling();
    return () => {
      if (bgLocationRef.current) clearInterval(bgLocationRef.current);
    };
  }, [startLocationPolling]);

  useEffect(() => {
    const unsub = NetInfo.addEventListener((s) =>
      setIsConnected(s.isConnected ?? false),
    );
    return unsub;
  }, []);

  useEffect(() => {
    const h = BackHandler.addEventListener("hardwareBackPress", () => {
      if (canGoBack) {
        webViewRef.current?.goBack();
        return true;
      }
      return false;
    });
    return () => h.remove();
  }, [canGoBack]);

  useEffect(
    () => () => {
      if (safetyTimer.current) clearTimeout(safetyTimer.current);
      if (fabTimerRef.current) clearTimeout(fabTimerRef.current);
      if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
    },
    [],
  );

  const clearSafetyTimer = useCallback(() => {
    if (safetyTimer.current) {
      clearTimeout(safetyTimer.current);
      safetyTimer.current = null;
    }
  }, []);

  const dismissLoading = useCallback(() => {
    const delay = Math.max(
      0,
      MIN_SPLASH_DURATION - (Date.now() - loadStartTime.current),
    );
    setTimeout(() => {
      Animated.timing(loadingFade, {
        toValue: 0,
        duration: 400,
        useNativeDriver: true,
      }).start(() => {
        setLoading(false);
        setIsRefreshing(false);
        setLoadProgress(1);
        fabTimerRef.current = setTimeout(() => setShowFab(true), 800);
      });
    }, delay);
  }, [loadingFade]);

  const retryLoad = useCallback(() => {
    setHasError(false);
    setLoading(true);
    setShowFab(false);
    setLoadProgress(0);
    loadingFade.setValue(1);
    loadStartTime.current = Date.now();
    webViewRef.current?.reload();
  }, [loadingFade]);

  const handleManualRefresh = useCallback(() => {
    if (isRefreshing || loading) return;
    setIsRefreshing(true);
    setShowFab(true);
    setLoadProgress(0);
    loadStartTime.current = Date.now();
    loadingFade.setValue(1);
    webViewRef.current?.reload();
  }, [isRefreshing, loading, loadingFade]);

  const handleGoBack = useCallback(() => webViewRef.current?.goBack(), []);

  const handleLoadStart = useCallback(() => {
    loadStartTime.current = Date.now();
    setLoading(true);
    setHasError(false);
    setLoadProgress(0);
    loadingFade.setValue(1);
    clearSafetyTimer();
    safetyTimer.current = setTimeout(dismissLoading, SAFETY_TIMEOUT);
  }, [loadingFade, clearSafetyTimer, dismissLoading]);

  const handleLoadProgress = useCallback(
    ({ nativeEvent }: { nativeEvent: { progress: number } }) => {
      setLoadProgress(nativeEvent.progress);
      if (nativeEvent.progress >= 0.9) {
        clearSafetyTimer();
        dismissLoading();
      }
    },
    [clearSafetyTimer, dismissLoading],
  );

  const handleLoadEnd = useCallback(() => {
  clearSafetyTimer();
  dismissLoading();
  injectLocation();
  injectDeviceToken();
}, [clearSafetyTimer, dismissLoading, injectLocation, injectDeviceToken]);

  const handleError = useCallback(() => {
    clearSafetyTimer();
    setLoading(false);
    setIsRefreshing(false);
    setHasError(true);
    setShowFab(false);
  }, [clearSafetyTimer]);

  if (!isConnected) {
    return (
      <SafeAreaView style={styles.safeArea}>
        <OverlayScreen
          title="No Connection"
          subtitle="Check your network and try again."
          showRetry
          retryLabel="Reconnect"
          onRetry={retryLoad}
        />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safeArea}>
      {/* Transparent touch-capture overlay — catches all gestures, passes them through */}
      <View
        style={StyleSheet.absoluteFill}
        {...panResponder.panHandlers}
        pointerEvents="box-none"
      />

      <WebView
        ref={webViewRef}
        source={{ uri: WEBSITE_URL }}
        javaScriptEnabled
        domStorageEnabled
        cacheEnabled
        mixedContentMode="always"
        allowFileAccess
        allowFileAccessFromFileURLs
        allowUniversalAccessFromFileURLs
        allowsFullscreenVideo
        allowsInlineMediaPlayback
        originWhitelist={["*"]}
        pullToRefreshEnabled
        geolocationEnabled
        onNavigationStateChange={(s) => setCanGoBack(s.canGoBack)}
        onLoadStart={handleLoadStart}
        onLoadProgress={handleLoadProgress}
        onLoadEnd={handleLoadEnd}
        onError={handleError}
        onHttpError={handleError}
        style={styles.webView}
      />

      {!hasError && (
        <TorusFAB
          onPress={handleManualRefresh}
          onLongPress={handleGoBack}
          isRefreshing={isRefreshing}
          visible={showFab && !loading}
          userVisible={userVisible}
          loadProgress={loadProgress}
          canGoBack={canGoBack}
        />
      )}

      {loading && (
        <Animated.View
          style={[StyleSheet.absoluteFill, { opacity: loadingFade }]}
          pointerEvents="none"
        >
          <OverlayScreen
            title={isRefreshing ? "Refreshing..." : "Loading..."}
          />
        </Animated.View>
      )}

      {hasError && (
        <View style={StyleSheet.absoluteFill}>
          <OverlayScreen
            title="Couldn't Load"
            subtitle="Something went wrong loading EnterGYM. Please try again."
            showRetry
            retryLabel="Try Again"
            onRetry={retryLoad}
          />
        </View>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1, backgroundColor: "#080808" },
  webView: { flex: 1 },

  overlayContainer: {
    flex: 1,
    backgroundColor: "#080808",
    justifyContent: "center",
    alignItems: "center",
  },
  accentLine: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    height: 3,
    backgroundColor: "#ff4d00",
  },
  overlayContent: { alignItems: "center", paddingHorizontal: 32 },
  dinoWrapper: {
    width: 180,
    height: 180,
    justifyContent: "center",
    alignItems: "center",
    marginBottom: 28,
  },
  dino: { width: 150, height: 150, zIndex: 2 },
  tag: {
    backgroundColor: "#ff4d0018",
    borderWidth: 1,
    borderColor: "#ff4d0055",
    borderRadius: 4,
    paddingHorizontal: 12,
    paddingVertical: 4,
    marginBottom: 16,
  },
  tagText: {
    color: "#ff4d00",
    fontSize: 10,
    fontWeight: "800",
    letterSpacing: 4,
  },
  overlayTitle: {
    color: "#fff",
    fontSize: 24,
    fontWeight: "800",
    letterSpacing: 0.5,
    textAlign: "center",
    marginBottom: 10,
  },
  overlaySubtitle: {
    color: "#888",
    fontSize: 14,
    textAlign: "center",
    lineHeight: 22,
    maxWidth: 280,
    marginBottom: 32,
  },
  retryButton: { marginTop: 8 },
  retryButtonInner: {
    backgroundColor: "#ff4d00",
    paddingHorizontal: 40,
    paddingVertical: 14,
    borderRadius: 8,
    borderBottomRightRadius: 2,
  },
  retryButtonText: {
    color: "#fff",
    fontWeight: "800",
    fontSize: 14,
    letterSpacing: 2,
    textTransform: "uppercase",
  },
  bottomDecor: {
    position: "absolute",
    bottom: 40,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingHorizontal: 24,
  },
  bottomBar: { flex: 1, height: 1, backgroundColor: "#222" },
  bottomLabel: {
    color: "#333",
    fontSize: 9,
    letterSpacing: 3,
    fontWeight: "600",
  },
  barsContainer: {
    position: "absolute",
    overflow: "hidden",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
  },
  bar: {
    position: "absolute",
    bottom: 0,
    width: 28,
    backgroundColor: "#ff4d00",
    borderTopLeftRadius: 4,
    borderTopRightRadius: 4,
  },

  // ── Torus FAB ──────────────────────────────────────────────────
  fabContainer: {
    position: "absolute",
    bottom: 36,
    right: 20,
    alignItems: "center",
    zIndex: 100,
  },

  torusPill: {
    marginTop: 6,
    backgroundColor: "#0e0e0e",
    borderWidth: 1,
    borderColor: "#1c1c1c",
    borderRadius: 4,
    paddingHorizontal: 8,
    paddingVertical: 3,
    alignItems: "center",
  },
  torusPillActive: { borderColor: "#ff4d0033" },
  torusPillDone: { borderColor: "#22c55e44" },

  torusPillText: {
    color: "#3a3a3a",
    fontSize: 8,
    fontWeight: "800",
    letterSpacing: 1.5,
  },
  torusPillTextActive: { color: "#ff4d00" },
  torusPillTextDone: { color: "#22c55e" },
});
