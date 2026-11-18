import Foundation
import Vision
import CoreGraphics
import ImageIO

struct TextLine: Encodable {
    let text: String
    let x: Double
    let y: Double
    let width: Double
    let height: Double
    let confidence: Float
    let source: String
}

do {
    guard CommandLine.arguments.count == 2 else {
        throw NSError(domain: "OCR", code: 1, userInfo: [NSLocalizedDescriptionKey: "이미지 경로가 필요합니다."])
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    let languages = try request.supportedRecognitionLanguages()
    guard languages.contains("ko-KR") else {
        throw NSError(domain: "OCR", code: 1, userInfo: [NSLocalizedDescriptionKey: "이 macOS 버전의 Vision은 한국어 인식을 지원하지 않습니다."])
    }
    request.recognitionLanguages = ["ko-KR", "en-US"].filter { languages.contains($0) }
    request.usesLanguageCorrection = false
    let url = URL(fileURLWithPath: CommandLine.arguments[1])
    try VNImageRequestHandler(url: url).perform([request])
    var lines: [TextLine] = (request.results ?? []).compactMap { observation in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        return TextLine(text: candidate.string, x: box.minX, y: box.minY,
                        width: box.width, height: box.height, confidence: candidate.confidence, source: "screen")
    }

    // Small status-bar glyphs can be misread; retry the whole strip and its left portion at 2x.
    if let source = CGImageSourceCreateWithURL(url as CFURL, nil),
       let image = CGImageSourceCreateImageAtIndex(source, 0, nil) {
        let cropHeight = max(1, Int(Double(image.height) * 0.045))
        for widthFraction in [0.3, 1.0] {
            let cropWidth = max(1, Int(Double(image.width) * widthFraction))
            if let cropped = image.cropping(to: CGRect(x: 0, y: 0, width: cropWidth, height: cropHeight)),
               let context = CGContext(data: nil, width: cropped.width * 2, height: cropped.height * 2,
                                       bitsPerComponent: 8, bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                                       bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) {
                context.interpolationQuality = .high
                context.draw(cropped, in: CGRect(x: 0, y: 0, width: cropped.width * 2, height: cropped.height * 2))
                if let scaled = context.makeImage() {
                    let statusRequest = VNRecognizeTextRequest()
                    statusRequest.recognitionLevel = .accurate
                    statusRequest.recognitionLanguages = request.recognitionLanguages
                    statusRequest.usesLanguageCorrection = false
                    if (try? VNImageRequestHandler(cgImage: scaled).perform([statusRequest])) != nil {
                        let fraction = Double(cropHeight) / Double(image.height)
                        let widthFraction = Double(cropWidth) / Double(image.width)
                        lines += (statusRequest.results ?? []).compactMap { observation in
                            guard let candidate = observation.topCandidates(1).first else { return nil }
                            let box = observation.boundingBox
                            return TextLine(text: candidate.string, x: box.minX * widthFraction,
                                            y: 1 - fraction + box.minY * fraction,
                                            width: box.width * widthFraction, height: box.height * fraction,
                                            confidence: candidate.confidence, source: "status_crop")
                        }
                    }
                }
            }
        }
    }
    let data = try JSONEncoder().encode(lines)
    FileHandle.standardOutput.write(data)
} catch {
    FileHandle.standardError.write(Data("\(error.localizedDescription)\n".utf8))
    exit(1)
}
