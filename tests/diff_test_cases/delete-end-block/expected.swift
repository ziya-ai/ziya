//
//  MIDIManager.swift
//  miditrim
//
//  Created by Cohn, Dan on [Current Date]
//
 
import Foundation
import CoreMIDI
import Combine
 
class MIDIManager: ObservableObject {
    @Published var outputDestinations: [MIDIEndpointRef] = []
    @Published var selectedDestinationIndex: Int? = nil {
        didSet {
            if let index = selectedDestinationIndex, outputDestinations.indices.contains(index) {
                selectedDestination = outputDestinations[index]
                print("MIDI Output selected: \(displayName(for: selectedDestination!))")
            } else {
                selectedDestination = nil
                print("MIDI Output deselected.")
            }
        }
    }
    
    private var selectedDestination: MIDIEndpointRef?
 
    private var midiClient = MIDIClientRef()
    private var outputPort = MIDIPortRef()
 
    init() {
        setupMIDI()
        refreshDestinations()
    }
 
    private func setupMIDI() {
        MIDIClientCreate("miditrim.MIDIManager" as CFString, nil, nil, &midiClient)
        MIDIOutputPortCreate(midiClient, "miditrim.OutputPort" as CFString, &outputPort)
        
        // Optional: Add notification observer for MIDI setup changes
        // Ensure CoreMIDI is properly linked and kMIDISetupNotification is accessible.
        // The following line is the standard way to use this notification.
        NotificationCenter.default.addObserver(self, selector: #selector(midiSetupChanged(_:)), name: NSNotification.Name(rawValue: kMIDISetupNotification as String), object: nil) 
    }
 
    @objc private func midiSetupChanged(_ notification: Notification) {
        print("MIDI Setup Changed. Refreshing destinations.")
        refreshDestinations()
    }
 
    func refreshDestinations() {
        let count = MIDIGetNumberOfDestinations()
        var destinations: [MIDIEndpointRef] = []
        for i in 0..<count {
            destinations.append(MIDIGetDestination(i))
        }
        self.outputDestinations = destinations
        
        // If a destination was previously selected, try to re-select it
        if let currentSelected = selectedDestination {
            if let newIndex = self.outputDestinations.firstIndex(of: currentSelected) {
                self.selectedDestinationIndex = newIndex
            } else {
                self.selectedDestinationIndex = nil // Previous selection no longer exists
            }
        } else if !destinations.isEmpty {
            // self.selectedDestinationIndex = 0 // Optionally auto-select the first one
        }
    }
 
    func displayName(for endpoint: MIDIEndpointRef) -> String {
        var param: Unmanaged<CFString>?
        var name: String = "Unknown Device"
        if MIDIObjectGetStringProperty(endpoint, kMIDIPropertyDisplayName, &param) == noErr {
            if let cfName = param?.takeRetainedValue() {
                name = cfName as String
            }
        }
        return name
    }
 
    func sendNoteOn(note: UInt8, velocity: UInt8, channel: UInt8 = 0) {
        guard let dest = selectedDestination else {
            print("Cannot send Note On: No MIDI destination selected.")
            return
        }
        var packet = MIDIPacket()
        packet.timeStamp = 0 // Send immediately
        packet.length = 3
        packet.data.0 = 0x90 + channel // Note On event on specified channel
        packet.data.1 = note & 0x7F // Note number (0-127)
        packet.data.2 = velocity & 0x7F // Velocity (0-127)
 
        var packetList = MIDIPacketList(numPackets: 1, packet: packet)
        MIDISend(outputPort, dest, &packetList)
        print("Sent Note On: \(note), Vel: \(velocity) to \(displayName(for: dest))")
    }
 
    func sendNoteOff(note: UInt8, channel: UInt8 = 0) {
        guard let dest = selectedDestination else {
            print("Cannot send Note Off: No MIDI destination selected.")
            return
        }
        var packet = MIDIPacket()
        packet.timeStamp = 0
        packet.length = 3
        packet.data.0 = 0x80 + channel // Note Off event
        packet.data.1 = note & 0x7F
        packet.data.2 = 0 // Velocity for Note Off is often 0
 
        var packetList = MIDIPacketList(numPackets: 1, packet: packet)
        MIDISend(outputPort, dest, &packetList)
        print("Sent Note Off: \(note) to \(displayName(for: dest))")
    }
    
    deinit {
        // Clean up CoreMIDI resources
        NotificationCenter.default.removeObserver(self)
        MIDIPortDispose(outputPort)
        MIDIClientDispose(midiClient)
        print("MIDIManager deinitialized.")
    }
}
 
