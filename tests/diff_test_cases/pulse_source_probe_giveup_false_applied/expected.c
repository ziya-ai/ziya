/* pulse_sync.c - generic pulse-source discipline loop (test fixture, MRE) */
#include <stdint.h>
#include <string.h>

typedef int STATUS_T;
typedef unsigned char U8_T;
typedef unsigned int U32_T;
#define STATUS_OK 0

typedef enum {
    PULSE_SOURCE_PIN_A = 0,
    PULSE_SOURCE_PIN_B = 1
} PulseSourceMode;

typedef struct {
    uint32_t nanoSeconds;
} PulseCapture;

static void logMessage(const char* fmt, ...) { (void)fmt; }
static STATUS_T hwPulseInModeSet(U8_T d, int n, PulseSourceMode m) { (void)d;(void)n;(void)m; return STATUS_OK; }
#define PULSE_IN_NUMBER_0 0
#define LOG_AND_RET_ERR(rc) if((rc) != STATUS_OK) { return (rc); }

/* Pulse validity thresholds. */
#define PULSE_GOOD_REQUIRED        3
#define PULSE_PHASE_MAX_NS         (100LL * 1000 * 1000)
/* dPhase this large between consecutive good captures cannot come
 * from a disciplined 1PPS-like source: at least one capture is noise. */
#define PULSE_PHASE_CONSISTENCY_NS (3LL * 1000 * 1000)

/* Two candidate input pins both reach the pulse-in through a mux, so
 * probe each candidate and keep whichever carries a valid pulse, so a
 * hardware revision is picked up without reconfiguration. */
#define PULSE_SOURCE_PROBE_TICKS 6

/* Give up probing after this many unsuccessful mux flips and settle on
 * software measurement.  A board with no pulse on either pin would
 * otherwise alternate the mux every PULSE_SOURCE_PROBE_TICKS forever,
 * logging repeatedly and making sourceSwitches useless as a diagnostic. */
#define PULSE_SOURCE_PROBE_GIVEUP 4

static int    pulseValid          = 0;
static int    pulseGoodStreak     = 0;
static int    pulseBadStreak      = 0;
static U32_T  pulseSourceSwitches = 0;
static int    pulseProbeGaveUp    = 0;
static int    pulseHaveGoodPhase  = 0;
static int64_t pulsePrevGoodPhaseNs = 0;
static U32_T  pulseInconsistent   = 0;
static PulseSourceMode pulseSourceMode = PULSE_SOURCE_PIN_A;
static PulseCapture pulseLastCapture;

static const char* pulseSourceName(PulseSourceMode mode)
{
    return (mode == PULSE_SOURCE_PIN_A) ? "PIN_A" : "PIN_B";
}

/* Distractor #1: pulseProbeGaveUp and pulseSourceSwitches are already
 * declared and already referenced elsewhere in the file (mux switch
 * bookkeeping and the status dump below), even though the specific
 * probe-giveup gating logic inside pulseMeasure()'s bad-streak branch
 * has NOT yet been added.  A text/context-based "already applied"
 * check that merely looks for these identifiers anywhere in the file
 * can be fooled into believing the hunk already landed. */
static STATUS_T pulseSourceSelect(U8_T devNum, PulseSourceMode mode)
{
    STATUS_T rc;

    rc = hwPulseInModeSet(devNum, PULSE_IN_NUMBER_0, mode);
    LOG_AND_RET_ERR(rc);

    pulseSourceMode    = mode;
    pulseValid         = 0;
    pulseGoodStreak    = 0;
    pulseBadStreak     = 0;
    pulseSourceSwitches++;
    logMessage("pulseSync: probing pulse on %s\n", pulseSourceName(mode));
    return STATUS_OK;
}

static int pulseMeasure(U8_T devNum, int64_t* phaseNsOut)
{
    PulseCapture cap;
    int64_t      phaseNs = 0;
    int          ok      = 1;

    memset(&cap, 0, sizeof(cap));

    if(ok)
    {
        phaseNs = (int64_t)cap.nanoSeconds;
        if(phaseNs >= 500000000LL)
        {
            phaseNs -= 1000000000LL;
        }
        if(phaseNs > PULSE_PHASE_MAX_NS ||
           phaseNs < -PULSE_PHASE_MAX_NS)
        {
            ok = 0; /* implausible: noise or a software-triggered latch */
        }
        else if(pulseHaveGoodPhase)
        {
            int64_t dPhase = phaseNs - pulsePrevGoodPhaseNs;

            if(dPhase < 0)
            {
                dPhase = -dPhase;
            }
            if(dPhase > PULSE_PHASE_CONSISTENCY_NS)
            {
                /* Two captures this far apart cannot both come from a
                 * disciplined source: at least one is noise. */
                ok = 0;
                pulseInconsistent++;
            }
        }
    }

    if(ok)
    {
        pulseLastCapture     = cap;
        pulseBadStreak       = 0;
        pulseProbeGaveUp     = 0; /* a live pulse re-arms probing */
        pulsePrevGoodPhaseNs = phaseNs;
        pulseHaveGoodPhase   = 1;
        if(pulseGoodStreak < PULSE_GOOD_REQUIRED)
        {
            pulseGoodStreak++;
        }
    }
    else
    {
        if(pulseValid)
        {
            logMessage("pulseSync: pulse validity lost, falling "
                       "back to software measurement\n");
        }
        pulseGoodStreak    = 0;
        pulseHaveGoodPhase = 0;

        /* Alternate the pulse-in mux between the two candidate pins
         * until one carries a valid pulse, so a hardware revision is
         * picked up without reconfiguration. */
        if(!pulseProbeGaveUp &&
           ++pulseBadStreak >= PULSE_SOURCE_PROBE_TICKS)
        {
            if(pulseSourceSwitches >= PULSE_SOURCE_PROBE_GIVEUP)
            {
                pulseProbeGaveUp = 1;
                logMessage("pulseSync: no pulse on either pin after "
                           "%u attempts; using software measurement\n",
                           pulseSourceSwitches);
            }
            else
            {
                (void)pulseSourceSelect(
                    devNum,
                    (pulseSourceMode == PULSE_SOURCE_PIN_A)
                        ? PULSE_SOURCE_PIN_B
                        : PULSE_SOURCE_PIN_A);
            }
        }
    }

    pulseValid = (pulseGoodStreak >= PULSE_GOOD_REQUIRED);
    if(pulseValid && ok)
    {
        *phaseNsOut = phaseNs;
        return 1;
    }
    return 0;
}

/* Distractor #2: status dump already prints pulseProbeGaveUp and
 * pulseSourceSwitches, reinforcing the false "already applied" signal. */
STATUS_T showStatus(void)
{
    logMessage(
        "  pulseSource=%s sourceSwitches=%u probeGaveUp=%d "
        "inconsistent=%u\n",
        pulseSourceName(pulseSourceMode),
        pulseSourceSwitches,
        pulseProbeGaveUp,
        pulseInconsistent);
    return STATUS_OK;
}
