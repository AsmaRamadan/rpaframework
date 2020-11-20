import time
from typing import List, Union

from RPA.Desktop.keywords import LibraryContext, keyword, screen
from RPA.core.geometry import Point, Region
from RPA.core.locators import (
    Coordinates,
    Offset,
    ImageTemplate,
    OCR,
    parse_locator,
)

try:
    from RPA.recognition import templates, ocr

    HAS_RECOGNITION = True
except ImportError:
    HAS_RECOGNITION = False


def ensure_recognition():
    if not HAS_RECOGNITION:
        raise ValueError(
            "Locator type not supported, please install the "
            "rpaframework-recognition package"
        )


def transform(regions: List[Region], source: Region, destination: Region):
    """Transform given regions from a local coordinate system to a
    global coordinate system.

    Takes into account location and scaling of the regions.
    Assumes that the aspect ratio does not change.

    :param regions: List of regions to transform
    :param source: Dimensions of local coordinate system
    :param destination: Position/scale of local coordinates in the global scope
    """
    scale = float(destination.height) / float(source.height)
    for region in regions:
        region.scale(scale)
        region.move(destination.left, destination.top)


class TimeoutException(ValueError):
    """Timeout reached while waiting for condition."""


class FinderKeywords(LibraryContext):
    """Keywords for locating elements."""

    def __init__(self, ctx):
        super().__init__(ctx)

        if HAS_RECOGNITION:
            self.confidence = templates.DEFAULT_CONFIDENCE
        else:
            self.confidence = None

    def _find(self, locator: str) -> List[Union[Point, Region]]:
        """Internal method for resolving and searching locators."""
        if isinstance(locator, (Region, Point)):
            return [locator]

        locator = parse_locator(locator)
        self.logger.info("Using locator: %s", locator)

        if isinstance(locator, Coordinates):
            position = Point(locator.x, locator.y)
            return [position]
        elif isinstance(locator, Offset):
            position = self.ctx.get_mouse_position()
            position.offset(locator.x, locator.y)
            return [position]
        elif isinstance(locator, ImageTemplate):
            ensure_recognition()
            return self._find_templates(locator)
        elif isinstance(locator, OCR):
            ensure_recognition()
            return self._find_ocr(locator)
        else:
            raise NotImplementedError(f"Unsupported locator: {locator}")

    def _find_templates(self, locator: ImageTemplate) -> List[Region]:
        """Find all regions that match given image template,
        inside the combined virtual display.
        """
        confidence = locator.confidence or self.confidence
        self.logger.info("Matching with confidence of %.1f", confidence)

        results = []

        for display in screen.displays():
            image = screen.grab(display)

            try:
                regions = templates.find(
                    image=image,
                    template=locator.path,
                    confidence=confidence,
                )
            except templates.ImageNotFoundError:
                continue

            for region in regions:
                screen.log_image(image.crop(region.as_tuple()), size=400)

            local = Region.from_size(0, 0, image.size[0], image.size[1])
            transform(regions, local, display)
            results.extend(regions)

        return results

    def _find_ocr(self, locator: OCR) -> List[Region]:
        """Find the position of all blocks of text that match the given string,
        inside the combined virtual display.
        """
        confidence = locator.confidence or self.confidence
        self.logger.info("Matching with confidence of %.1f", confidence)

        results = []

        for display in screen.displays():
            image = screen.grab(display)

            matches = ocr.find(
                image=image,
                text=locator.text,
                confidence=confidence,
            )

            regions = [match["region"] for match in matches]
            for region in regions:
                screen.log_image(image.crop(region.as_tuple()), size=400)

            local = Region.from_size(0, 0, image.size[0], image.size[1])
            transform(regions, local, display)
            results.extend(regions)

        return results

    @keyword
    def find_elements(self, locator: str) -> List[Union[Point, Region]]:
        """Find all elements defined by locator, and return their positions.

        :param locator: Locator string

        Example:

        .. code-block:: robotframework

            ${matches}=    Find elements    image:icon.png
            FOR    ${match}  IN  @{matches}
                Log    Found icon at ${match.x}, ${match.y}
            END
        """
        matches = self._find(locator)

        display = self.ctx.get_display_dimensions()
        for match in matches:
            if not display.contains(match):
                self.logger.warning("Match outside display bounds: %s", match)

        return matches

    @keyword
    def find_element(self, locator: str) -> Union[Point, Region]:
        """Find an element defined by locator, and return its position.

        :param locator: Locator string

        Example:

        .. code-block:: robotframework

            ${match}=    Find element    image:logo.png
            Log    Found logo at ${match.x}, ${match.y}
        """
        matches = self.find_elements(locator)

        if not matches:
            raise ValueError(f"No matches found for: {locator}")

        if len(matches) > 1:
            # TODO: Add run-on-error support and maybe screenshotting matches?
            raise ValueError(
                "Found {count} matches for: {locator} at locations {matches}".format(
                    count=len(matches), locator=locator, matches=matches
                )
            )

        return matches[0]

    @keyword
    def wait_for_element(
        self, locator: str, timeout: float = 10.0, interval: float = 0.5
    ) -> Point:
        """Wait for an element defined by locator to exist or
        until timeout is reached.

        :param locator: Locator string

        Example:

        .. code-block:: robotframework

            Wait for element    alias:CookieConsent    timeout=30
            Click    image:%{ROBOT_ROOT}/accept.png
        """
        interval = float(interval)
        end_time = time.time() + float(timeout)

        while time.time() <= end_time:
            try:
                return self.find_element(locator)
            except ValueError:
                time.sleep(interval)

        raise TimeoutException(f"No element found within timeout: {locator}")

    @keyword
    def set_default_confidence(self, confidence: float):
        """Set the default template matching confidence.

        :param confidence: Value from 1 to 100
        """
        confidence = float(confidence)
        confidence = min(confidence, 100.0)
        confidence = max(confidence, 1.0)
        self.confidence = confidence
