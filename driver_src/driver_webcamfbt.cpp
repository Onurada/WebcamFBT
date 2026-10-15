// WebcamFBT - SteamVR driver that exposes virtual body trackers.
// Poses come over UDP (127.0.0.1:39570) from tracker/webcam_fbt.py.
// Poses are expected in SteamVR raw tracking space (the same space as the HMD).

#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

#include <atomic>
#include <chrono>
#include <cmath>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>

#include "third_party/openvr_driver.h"

#pragma comment(lib, "ws2_32.lib")

using namespace vr;

static const int kUdpPort = 39570;
static const uint32_t kMagic = 0x54424657; // "WFBT"
static const int kMaxTrackers = 8;
// Bump together with PROTOCOL in tracker/webcam_fbt.py. The engine reads it back from the
// trackers' Prop_DriverVersion_String and warns when SteamVR still runs an older driver.
// 1 = original, 2 = version trailer after the tracker records.
static const uint32_t kProtocol = 2;

struct TrackerInfo {
    const char *serial;
    const char *role;
};

// Order must match TRACKERS in webcam_fbt.py
static const TrackerInfo kTrackers[kMaxTrackers] = {
    {"WCFBT_HIP", "TrackerRole_Waist"},
    {"WCFBT_LFOOT", "TrackerRole_LeftFoot"},
    {"WCFBT_RFOOT", "TrackerRole_RightFoot"},
    {"WCFBT_LKNEE", "TrackerRole_LeftKnee"},
    {"WCFBT_RKNEE", "TrackerRole_RightKnee"},
    {"WCFBT_CHEST", "TrackerRole_Chest"},
    {"WCFBT_LELBOW", "TrackerRole_LeftElbow"},
    {"WCFBT_RELBOW", "TrackerRole_RightElbow"},
};

#pragma pack(push, 1)
struct PacketHeader {
    uint32_t magic;
    uint32_t count;
};
struct PacketTracker {
    uint32_t id;
    uint32_t flags; // bit0 = pose valid
    float pos[3];
    float rot[4]; // w x y z
};
#pragma pack(pop)

using Clock = std::chrono::steady_clock;

// The webcam delivers ~20 poses/s while the headset renders at 72-120 Hz. Showing each
// packet as-is makes trackers step visibly, so every new packet is blended in over one
// packet interval, starting from whatever pose is on screen at that moment.
struct SharedPose {
    bool seen = false;
    bool valid = false;
    float pos[3] = {0, 0, 0};
    float rot[4] = {1, 0, 0, 0}; // w x y z
    float fromPos[3] = {0, 0, 0};
    float fromRot[4] = {1, 0, 0, 0};
    float interval = 0.05f; // seconds between packets (smoothed)
    Clock::time_point stamp;
};

static void Interpolate(const SharedPose &p, Clock::time_point now, float outPos[3], float outRot[4]) {
    float a = std::chrono::duration<float>(now - p.stamp).count() / p.interval;
    a = a < 0 ? 0 : (a > 1 ? 1 : a);
    for (int i = 0; i < 3; i++) outPos[i] = p.fromPos[i] + (p.pos[i] - p.fromPos[i]) * a;

    // nlerp along the shorter arc
    float dot = 0;
    for (int i = 0; i < 4; i++) dot += p.fromRot[i] * p.rot[i];
    float sign = dot < 0 ? -1.0f : 1.0f;
    float len = 0;
    for (int i = 0; i < 4; i++) {
        outRot[i] = p.fromRot[i] * (1 - a) + p.rot[i] * sign * a;
        len += outRot[i] * outRot[i];
    }
    len = std::sqrt(len);
    if (len < 1e-6f) {
        memcpy(outRot, p.rot, sizeof(p.rot));
        return;
    }
    for (int i = 0; i < 4; i++) outRot[i] /= len;
}

static void ApplySample(SharedPose &p, const PacketTracker &t, Clock::time_point now) {
    float gap = std::chrono::duration<float>(now - p.stamp).count();
    if (p.seen && gap < 0.25f) {
        // Blend from the pose currently being shown to the new one.
        Interpolate(p, now, p.fromPos, p.fromRot);
        float interval = p.interval * 0.8f + gap * 0.2f;
        p.interval = interval < 0.01f ? 0.01f : (interval > 0.12f ? 0.12f : interval);
    } else {
        // First packet or after a pause: jump straight there.
        memcpy(p.fromPos, t.pos, sizeof(p.fromPos));
        memcpy(p.fromRot, t.rot, sizeof(p.fromRot));
    }
    p.seen = true;
    p.valid = (t.flags & 1) != 0;
    memcpy(p.pos, t.pos, sizeof(p.pos));
    memcpy(p.rot, t.rot, sizeof(p.rot));
    p.stamp = now;
}

static void Log(const char *msg) {
    if (VRDriverLog()) VRDriverLog()->Log(msg);
}

// ---------------------------------------------------------------------------

class VirtualTracker : public ITrackedDeviceServerDriver {
public:
    explicit VirtualTracker(int index) : index_(index) {}

    EVRInitError Activate(uint32_t objectId) override {
        objectId_ = objectId;
        PropertyContainerHandle_t props = VRProperties()->TrackedDeviceToPropertyContainer(objectId);
        const TrackerInfo &info = kTrackers[index_];

        VRProperties()->SetStringProperty(props, Prop_ModelNumber_String, "WebcamFBT Tracker");
        VRProperties()->SetStringProperty(props, Prop_ManufacturerName_String, "WebcamFBT");
        VRProperties()->SetStringProperty(props, Prop_TrackingSystemName_String, "webcamfbt");
        VRProperties()->SetStringProperty(props, Prop_SerialNumber_String, info.serial);
        VRProperties()->SetStringProperty(props, Prop_RenderModelName_String, "{htc}vr_tracker_vive_1_0");
        VRProperties()->SetStringProperty(props, Prop_ControllerType_String, "vive_tracker");
        VRProperties()->SetStringProperty(props, Prop_InputProfilePath_String, "{htc}/input/vive_tracker_profile.json");
        VRProperties()->SetInt32Property(props, Prop_ControllerRoleHint_Int32, TrackedControllerRole_OptOut);
        VRProperties()->SetBoolProperty(props, Prop_WillDriftInYaw_Bool, false);
        VRProperties()->SetBoolProperty(props, Prop_DeviceIsWireless_Bool, false);
        VRProperties()->SetBoolProperty(props, Prop_DeviceProvidesBatteryStatus_Bool, false);
        VRProperties()->SetStringProperty(props, Prop_DriverVersion_String, std::to_string(kProtocol).c_str());

        // Assign the body role so VRChat etc. pick it up without manual setup.
        std::string key = std::string("/devices/webcamfbt/") + info.serial;
        VRSettings()->SetString(k_pch_Trackers_Section, key.c_str(), info.role);
        return VRInitError_None;
    }

    void Deactivate() override { objectId_ = k_unTrackedDeviceIndexInvalid; }
    void EnterStandby() override {}
    void *GetComponent(const char *) override { return nullptr; }
    void DebugRequest(const char *, char *buf, uint32_t size) override {
        if (size) buf[0] = 0;
    }
    DriverPose_t GetPose() override { return pose_; }

    void Update(const SharedPose &p, Clock::time_point now) {
        if (objectId_ == k_unTrackedDeviceIndexInvalid) return;
        float pos[3], rot[4];
        Interpolate(p, now, pos, rot);
        DriverPose_t pose = {};
        pose.qWorldFromDriverRotation.w = 1;
        pose.qDriverFromHeadRotation.w = 1;
        pose.deviceIsConnected = true;
        pose.poseIsValid = p.valid && now - p.stamp < std::chrono::milliseconds(1000);
        pose.result = pose.poseIsValid ? TrackingResult_Running_OK : TrackingResult_Running_OutOfRange;
        pose.vecPosition[0] = pos[0];
        pose.vecPosition[1] = pos[1];
        pose.vecPosition[2] = pos[2];
        pose.qRotation.w = rot[0];
        pose.qRotation.x = rot[1];
        pose.qRotation.y = rot[2];
        pose.qRotation.z = rot[3];
        pose_ = pose;
        VRServerDriverHost()->TrackedDevicePoseUpdated(objectId_, pose_, sizeof(DriverPose_t));
    }

private:
    int index_;
    uint32_t objectId_ = k_unTrackedDeviceIndexInvalid;
    DriverPose_t pose_ = {};
};

// ---------------------------------------------------------------------------

class Provider : public IServerTrackedDeviceProvider {
public:
    EVRInitError Init(IVRDriverContext *ctx) override {
        VR_INIT_SERVER_DRIVER_CONTEXT(ctx);
        Log("[webcamfbt] Init\n");

        WSADATA wsa;
        if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) return VRInitError_Driver_Failed;
        sock_ = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        if (sock_ == INVALID_SOCKET) return VRInitError_Driver_Failed;
        sockaddr_in addr = {};
        addr.sin_family = AF_INET;
        addr.sin_port = htons(kUdpPort);
        inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);
        if (bind(sock_, (sockaddr *)&addr, sizeof(addr)) == SOCKET_ERROR) {
            Log("[webcamfbt] UDP bind failed (port in use?)\n");
            closesocket(sock_);
            sock_ = INVALID_SOCKET;
            return VRInitError_Driver_Failed;
        }
        DWORD timeoutMs = 200;
        setsockopt(sock_, SOL_SOCKET, SO_RCVTIMEO, (const char *)&timeoutMs, sizeof(timeoutMs));

        running_ = true;
        thread_ = std::thread(&Provider::ReceiveLoop, this);
        return VRInitError_None;
    }

    void Cleanup() override {
        running_ = false;
        if (thread_.joinable()) thread_.join();
        if (sock_ != INVALID_SOCKET) closesocket(sock_);
        sock_ = INVALID_SOCKET;
        WSACleanup();
        for (auto &t : trackers_) {
            delete t;
            t = nullptr;
        }
        VR_CLEANUP_SERVER_DRIVER_CONTEXT();
    }

    const char *const *GetInterfaceVersions() override { return k_InterfaceVersions; }
    bool ShouldBlockStandbyMode() override { return false; }
    void EnterStandby() override {}
    void LeaveStandby() override {}

    void RunFrame() override {
        SharedPose snapshot[kMaxTrackers];
        {
            std::lock_guard<std::mutex> lock(mutex_);
            for (int i = 0; i < kMaxTrackers; i++) snapshot[i] = poses_[i];
        }
        auto now = Clock::now();
        for (int i = 0; i < kMaxTrackers; i++) {
            if (!snapshot[i].seen) continue;
            if (!trackers_[i]) {
                // Trackers are created lazily, only for body parts the app actually sends.
                trackers_[i] = new VirtualTracker(i);
                VRServerDriverHost()->TrackedDeviceAdded(kTrackers[i].serial, TrackedDeviceClass_GenericTracker, trackers_[i]);
            }
            trackers_[i]->Update(snapshot[i], now);
        }
    }

private:
    void ReceiveLoop() {
        char buf[2048];
        while (running_) {
            int n = recv(sock_, buf, sizeof(buf), 0);
            if (n < (int)sizeof(PacketHeader)) continue;
            PacketHeader hdr;
            memcpy(&hdr, buf, sizeof(hdr));
            if (hdr.magic != kMagic || hdr.count > kMaxTrackers) continue;
            size_t records = sizeof(PacketHeader) + hdr.count * sizeof(PacketTracker);
            if (n < (int)records) continue;
            // Protocol 2+ engines append their version; older drivers simply ignore the extra bytes.
            uint32_t engineProtocol = 1;
            if (n >= (int)(records + sizeof(uint32_t))) memcpy(&engineProtocol, buf + records, sizeof(engineProtocol));
            if (engineProtocol != kProtocol && !protocolWarned_) {
                protocolWarned_ = true;
                std::string msg = "[webcamfbt] engine protocol " + std::to_string(engineProtocol) +
                                  " != driver protocol " + std::to_string(kProtocol) + "\n";
                Log(msg.c_str());
            }

            auto now = Clock::now();
            std::lock_guard<std::mutex> lock(mutex_);
            for (uint32_t i = 0; i < hdr.count; i++) {
                PacketTracker t;
                memcpy(&t, buf + sizeof(PacketHeader) + i * sizeof(PacketTracker), sizeof(t));
                if (t.id >= kMaxTrackers) continue;
                ApplySample(poses_[t.id], t, now);
            }
        }
    }

    SOCKET sock_ = INVALID_SOCKET;
    std::atomic<bool> running_{false};
    bool protocolWarned_ = false;
    std::thread thread_;
    std::mutex mutex_;
    SharedPose poses_[kMaxTrackers];
    VirtualTracker *trackers_[kMaxTrackers] = {};
};

static Provider g_provider;

extern "C" __declspec(dllexport) void *HmdDriverFactory(const char *interfaceName, int *returnCode) {
    if (strcmp(interfaceName, IServerTrackedDeviceProvider_Version) == 0) return &g_provider;
    if (returnCode) *returnCode = VRInitError_Init_InterfaceNotFound;
    return nullptr;
}
