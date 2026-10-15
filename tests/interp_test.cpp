// Driver interpolation test: feeds 20 Hz packets of a tracker moving at 1 m/s (1 cm noise) and samples
// like a 90 Hz RunFrame. Fails if the shown pose still jumps like the raw packets do.
// Build & run from a VS x64 prompt in this folder:
//   cl /nologo /O2 /EHsc /std:c++17 /DNOMINMAX interp_test.cpp ws2_32.lib && interp_test.exe
#include "../driver_src/driver_webcamfbt.cpp"
#include <algorithm>
#include <cstdio>
#include <random>

int main() {
    SharedPose p;
    std::mt19937 rng(1);
    std::normal_distribution<float> noise(0, 0.01f);
    auto t0 = Clock::now();
    float lastRaw = 0, lastSmooth = 0, maxRawStep = 0, maxSmoothStep = 0;
    int nextPacket = 0;
    for (int frame = 0; frame < 90 * 3; frame++) {
        auto now = t0 + std::chrono::microseconds(frame * 11111);
        while (nextPacket * 50000 <= frame * 11111) {
            PacketTracker t = {};
            t.flags = 1;
            t.rot[0] = 1;
            t.pos[0] = nextPacket * 0.05f + noise(rng);
            ApplySample(p, t, t0 + std::chrono::microseconds(nextPacket * 50000));
            nextPacket++;
        }
        float pos[3], rot[4];
        Interpolate(p, now, pos, rot);
        if (frame > 0) {
            maxRawStep = std::max(maxRawStep, std::fabs(p.pos[0] - lastRaw));
            maxSmoothStep = std::max(maxSmoothStep, std::fabs(pos[0] - lastSmooth));
        }
        lastRaw = p.pos[0];
        lastSmooth = pos[0];
    }
    printf("max per-frame jump: raw %.1f cm, interpolated %.1f cm (ideal 1.1 cm)\n", maxRawStep * 100, maxSmoothStep * 100);
    if (maxSmoothStep > 0.03f) {
        printf("FAIL\n");
        return 1;
    }
    printf("ok\n");
    return 0;
}
